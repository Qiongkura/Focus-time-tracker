"""SQLite 存储：会话记录与聚合查询。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .config import project_root
from .utils import clean_site_text, normalize_site_key

# schema 版本：改动 sessions 结构或分类回填逻辑时 +1。
# 只有版本变化（首次建库 / 升级）才触发全量回填，见 _migrate。
_SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    process TEXT NOT NULL,
    exe_path TEXT NOT NULL DEFAULT '',
    window_title TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '应用',
    site TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_process ON sessions(process);
"""

_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_EXTRA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sessions_category ON sessions(category);
CREATE INDEX IF NOT EXISTS idx_sessions_site ON sessions(site);
-- 组合索引：GUI 的聚合查询固定是「start_time 区间 + 分类/进程过滤」，
-- 只有单列索引时仍需回表过滤，组合索引可以直接定位区间
CREATE INDEX IF NOT EXISTS idx_sessions_start_category ON sessions(start_time, category);
CREATE INDEX IF NOT EXISTS idx_sessions_start_process ON sessions(start_time, process);
"""

_BROWSER_PROCESSES = ("chrome.exe", "msedge.exe", "firefox.exe")


def _split_by_day(start: datetime, end: datetime):
    """跨天的会话按天拆分为多段。"""
    parts = []
    cur = start
    while cur < end:
        next_midnight = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seg_end = min(end, next_midnight)
        if seg_end > cur:
            parts.append((cur, seg_end))
        cur = seg_end
    return parts


class UsageDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：监控线程写、GUI 线程读
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._migrate()
        self.conn.executescript(_EXTRA_INDEXES)
        self.conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        with self._lock:
            self.conn.close()

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "category" not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN category TEXT NOT NULL DEFAULT '应用'")
        if "site" not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN site TEXT NOT NULL DEFAULT ''")
        if "url" not in cols:
            self.conn.execute("ALTER TABLE sessions ADD COLUMN url TEXT NOT NULL DEFAULT ''")

        self.conn.executescript(_METADATA_SCHEMA)

        if self._get_meta_int("schema_version", 0) < _SCHEMA_VERSION:
            # 首次建库 / 版本升级：只有这一次需要全量回填
            self._reclassify_all()
            self._set_meta("schema_version", str(_SCHEMA_VERSION))
        else:
            # 常规启动：规则文件没变就完全跳过，不再每次扫全表
            self._reclassify_if_rules_changed()
        self.conn.commit()

    def _reclassify_all(self):
        """全量回填：浏览器会话分类、游戏规则、手动覆盖。"""
        self.conn.execute(
            "UPDATE sessions SET category='网站' WHERE category='应用' AND site='' "
            "AND lower(process) IN (?, ?, ?)",
            _BROWSER_PROCESSES,
        )
        self._backfill_games()
        self.apply_overrides()
        # 同时记下当前文件指纹：否则下次启动会误判为「规则变了」再全量跑一遍
        self._set_meta("classification_stamp", self._classification_stamp())

    def _reclassify_if_rules_changed(self):
        """游戏规则或手动覆盖文件变化时才重新分类。

        历史数据积累到几十万条以后，每次启动都全表跑一遍 ``is_game()`` 会让
        启动时间随使用时长线性增长。这里用文件指纹把「规则没变」的情况直接
        跳过——正常启动只读两个文件的 stat，不碰 sessions 表。
        """
        stamp = self._classification_stamp()
        if stamp == self._get_meta("classification_stamp", ""):
            return
        self._backfill_games()
        self.apply_overrides()
        self._set_meta("classification_stamp", stamp)

    @staticmethod
    def _file_stamp(path: Path) -> str:
        try:
            st = path.stat()
        except OSError:
            return ""
        return f"{st.st_mtime_ns}:{st.st_size}"

    def _classification_stamp(self) -> str:
        from .games import GAME_RULES_FILENAME
        from .overrides import OVERRIDES_FILENAME

        root = project_root()
        return "|".join((
            self._file_stamp(root / GAME_RULES_FILENAME),
            self._file_stamp(root / OVERRIDES_FILENAME),
        ))

    # ---------- metadata ----------

    def _get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def _get_meta_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._get_meta(key, ""))
        except (TypeError, ValueError):
            return default

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def schema_version(self) -> int:
        """当前数据库的 schema 版本（供 doctor 自检展示）。"""
        return self._get_meta_int("schema_version", 0)

    def _backfill_games(self):
        """用游戏规则引擎回填旧数据：把能识别为游戏的“应用”会话改为“游戏”。"""
        from .games import is_game

        rows = self.conn.execute(
            "SELECT id, process, exe_path, window_title FROM sessions WHERE category='应用'"
        ).fetchall()
        game_ids = [r[0] for r in rows if is_game(r[2], r[1], r[3])]
        for i in range(0, len(game_ids), 400):
            chunk = game_ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            self.conn.execute(
                f"UPDATE sessions SET category='游戏' WHERE id IN ({marks})", chunk
            )

    def apply_overrides(self):
        """把手动分类覆盖应用到已有记录（只影响 应用/游戏 两类，网站/系统不动）。"""
        from .overrides import all_overrides

        overrides = all_overrides()
        if not overrides:
            return
        with self._lock:
            for process, category in overrides.items():
                self.conn.execute(
                    "UPDATE sessions SET category=? WHERE lower(process)=? "
                    "AND category IN ('应用', '游戏')",
                    (category, process),
                )
            self.conn.commit()

    def reclassify_process(self, process: str):
        """清除手动覆盖后，按规则引擎重新判定某进程已有记录的 应用/游戏 分类。"""
        from .games import is_game
        from .overrides import override_for

        name = (process or "").strip().lower()
        if not name or override_for(process):
            return
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, exe_path, window_title FROM sessions "
                "WHERE lower(process)=? AND category IN ('应用', '游戏')",
                (name,),
            ).fetchall()
            game_ids = [r[0] for r in rows if is_game(r[1], process, r[2])]
            app_ids = [r[0] for r in rows if not is_game(r[1], process, r[2])]
            for ids, category in ((game_ids, "游戏"), (app_ids, "应用")):
                if ids:
                    marks = ",".join("?" * len(ids))
                    self.conn.execute(
                        f"UPDATE sessions SET category=? WHERE id IN ({marks})",
                        [category] + ids,
                    )
            self.conn.commit()

    def add_session(self, start: datetime, end: datetime, process: str, exe_path: str = "",
                    title: str = "", category: str = "应用", site: str = "", url: str = "") -> None:
        with self._lock:
            for s, e in _split_by_day(start, end):
                duration = (e - s).total_seconds()
                if duration <= 0:
                    continue
                self.conn.execute(
                    "INSERT INTO sessions (process, exe_path, window_title, category, site, url, "
                    "start_time, end_time, duration) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (process, exe_path or "", title or "", category or "应用", site or "",
                     url or "", s.isoformat(timespec="seconds"), e.isoformat(timespec="seconds"),
                     round(duration, 3)),
                )
            self.conn.commit()

    def summary_between(self, start: datetime, end: datetime):
        """按进程聚合 [start, end) 区间的总时长（CLI 报告用）。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT process, SUM(duration) AS total FROM sessions "
                "WHERE start_time >= ? AND start_time < ? "
                "GROUP BY process ORDER BY total DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return [{"process": r[0], "seconds": r[1]} for r in rows]

    def desktop_summary_between(self, start: datetime, end: datetime):
        """应用 + 游戏按进程聚合。

        展示分类取「该进程在区间内时长最长的那个分类」，而不是原来的
        ``MAX(category)``。``MAX`` 是按字符串排序取最大值，既不代表真实分类，
        也不代表占用时间最多的分类——同一个进程历史上既当过应用又当过游戏时，
        会出现「总时长包含两类、分类却只显示其中一个」的自相矛盾结果。
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT process, category, SUM(duration) AS total FROM sessions "
                "WHERE start_time >= ? AND start_time < ? AND category IN ('应用', '游戏') "
                "GROUP BY process, category",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()

        agg: dict[str, dict] = {}
        for process, category, total in rows:
            item = agg.setdefault(process, {"process": process, "category": category,
                                            "seconds": 0.0, "_top": -1.0})
            item["seconds"] += total
            if total > item["_top"]:
                item["category"] = category
                item["_top"] = total

        result = sorted(agg.values(), key=lambda x: x["seconds"], reverse=True)
        for item in result:
            del item["_top"]
        return result

    def sites_summary_between(self, start: datetime, end: datetime):
        """网站按站点聚合，显示最新的页面标题。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT site, window_title, url, start_time, duration FROM sessions "
                "WHERE start_time >= ? AND start_time < ? AND category='网站' AND site <> '' "
                "ORDER BY start_time DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        agg: dict[str, dict] = {}
        for site, title, url, _, duration in rows:
            # 用清洗后的站点 key 分组：忽略“和另外 N 个页面”、浏览器后缀、
            # www. 前缀、URL 路径等，同一个网站只算一条
            key = normalize_site_key(site) or "网页"
            item = agg.setdefault(
                key, {"site": "", "seconds": 0.0, "count": 0,
                      "title": "", "url": "", "_has_title": False,
                      "_has_domain": False})
            item["seconds"] += duration
            item["count"] += 1
            if title and not item["_has_title"]:
                item["title"] = clean_site_text(title) or title
                item["url"] = url or item["url"]
                item["_has_title"] = True
            # 展示名优先用域名形式（含点、无空格），比标题兜底干净
            if not item["_has_domain"] and site and "." in site and " " not in site:
                d = site.split("/", 1)[0].split("?", 1)[0]
                if d.startswith("www."):
                    d = d[4:]
                item["site"] = d
                item["_has_domain"] = True
            if not item["site"]:
                item["site"] = clean_site_text(site) or site
        result = [v for k, v in agg.items()]
        result.sort(key=lambda x: x["seconds"], reverse=True)
        return result

    def category_summary_between(self, start: datetime, end: datetime):
        """按分类聚合：{分类: {"seconds": …, "count": …}}。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT category, SUM(duration), COUNT(*) FROM sessions "
                "WHERE start_time >= ? AND start_time < ? GROUP BY category",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return {r[0]: {"seconds": r[1], "count": r[2]} for r in rows}

    def total_seconds_between(self, start: datetime, end: datetime, category: str | None = None):
        with self._lock:
            if category:
                row = self.conn.execute(
                    "SELECT COALESCE(SUM(duration), 0) FROM sessions "
                    "WHERE start_time >= ? AND start_time < ? AND category = ?",
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), category),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT COALESCE(SUM(duration), 0) FROM sessions "
                    "WHERE start_time >= ? AND start_time < ?",
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchone()
        return row[0]

    def daily_breakdown(self, days: int):
        """返回 {日期: {进程: 秒数}}（CLI 报告用）。"""
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        with self._lock:
            rows = self.conn.execute(
                "SELECT date(start_time) AS d, process, SUM(duration) AS total FROM sessions "
                "WHERE start_time >= ? GROUP BY d, process",
                (start.isoformat(timespec="seconds"),),
            ).fetchall()
        out: dict[str, dict[str, float]] = {}
        for d, process, total in rows:
            day = out.setdefault(d, {})
            day[process] = day.get(process, 0.0) + total
        return out

    def daily_category_totals(self, days: int):
        """返回 {日期: {分类: 秒数}}。"""
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
        with self._lock:
            rows = self.conn.execute(
                "SELECT date(start_time) AS d, category, SUM(duration) AS total FROM sessions "
                "WHERE start_time >= ? GROUP BY d, category",
                (start.isoformat(timespec="seconds"),),
            ).fetchall()
        out: dict[str, dict[str, float]] = {}
        for d, category, total in rows:
            day = out.setdefault(d, {})
            day[category] = day.get(category, 0.0) + total
        return out

    def recent_sessions_between(self, start: datetime, end: datetime, limit: int = 300):
        with self._lock:
            rows = self.conn.execute(
                "SELECT process, window_title, site, url, category, start_time, end_time, duration "
                "FROM sessions WHERE start_time >= ? AND start_time < ? "
                "ORDER BY start_time DESC LIMIT ?",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), limit),
            ).fetchall()
        return [
            {
                "process": r[0], "title": r[1], "site": r[2], "url": r[3],
                "category": r[4], "start": r[5], "end": r[6], "duration": r[7],
            }
            for r in rows
        ]

    # ---------- 数据保留与维护 ----------

    def count_sessions(self) -> int:
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    def purge_sessions_before(self, cutoff: datetime) -> int:
        """删除 ``start_time < cutoff`` 的会话，返回删除行数。"""
        with self._lock:
            cur = self.conn.execute(
                "DELETE FROM sessions WHERE start_time < ?",
                (cutoff.isoformat(timespec="seconds"),),
            )
            self.conn.commit()
            return cur.rowcount or 0

    def purge_all_sessions(self) -> int:
        """清空全部历史记录（「清除全部历史」用）。"""
        with self._lock:
            cur = self.conn.execute("DELETE FROM sessions")
            self.conn.commit()
            return cur.rowcount or 0

    def vacuum(self) -> None:
        """回收删除后的空洞、压缩数据库文件。

        VACUUM 不能在事务里执行，因此先把连接切到自动提交模式。
        """
        with self._lock:
            self.conn.commit()
            previous = self.conn.isolation_level
            self.conn.isolation_level = None
            try:
                self.conn.execute("VACUUM")
            finally:
                self.conn.isolation_level = previous

    def find_overlapping_sessions(self, since: datetime | None = None,
                                  limit: int = 20) -> list[dict]:
        """查找时间上互相重叠的会话对（重复统计的迹象）。

        原实现是无条件的全表自连接（``a JOIN b ON a.id < b.id``），数据量
        上来后接近 O(n²)。这里支持按时间范围先筛候选、并限制返回条数，
        默认只看最近一段时间的记录。
        """
        params: list = []
        where = ""
        if since is not None:
            stamp = since.isoformat(timespec="seconds")
            where = "WHERE a.start_time >= ? AND b.start_time >= ?"
            params = [stamp, stamp]
        sql = (
            "SELECT a.id, b.id, a.process, b.process, a.start_time, a.end_time, "
            "b.start_time, b.end_time "
            "FROM sessions a JOIN sessions b ON a.id < b.id "
            "AND a.start_time < b.end_time AND b.start_time < a.end_time "
            f"{where} LIMIT ?"
        )
        params.append(int(limit))
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [
            {"a_id": r[0], "b_id": r[1], "a_process": r[2], "b_process": r[3],
             "a_start": r[4], "a_end": r[5], "b_start": r[6], "b_end": r[7]}
            for r in rows
        ]
