"""SQLite 存储：会话记录与聚合查询。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .utils import clean_site_text, normalize_site_key

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

_EXTRA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sessions_category ON sessions(category);
CREATE INDEX IF NOT EXISTS idx_sessions_site ON sessions(site);
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
        # 旧数据回填分类
        self.conn.execute(
            "UPDATE sessions SET category='网站' WHERE category='应用' AND site='' "
            "AND lower(process) IN (?, ?, ?)",
            _BROWSER_PROCESSES,
        )
        self._backfill_games()
        self.apply_overrides()
        self.conn.commit()

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
        """应用 + 游戏按进程聚合。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT process, MAX(category) AS cat, SUM(duration) AS total FROM sessions "
                "WHERE start_time >= ? AND start_time < ? AND category IN ('应用', '游戏') "
                "GROUP BY process ORDER BY total DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return [{"process": r[0], "category": r[1], "seconds": r[2]} for r in rows]

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
