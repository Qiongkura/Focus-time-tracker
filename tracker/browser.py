"""浏览器当前网页识别。

原理：浏览器窗口标题通常包含当前页面的标题；浏览器把访问记录写入 SQLite
历史库。我们用窗口标题去历史库里匹配最近访问的页面，从而得到“正在看哪个网站”。
支持 Chrome / Edge（同一套历史库结构）和 Firefox。
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .utils import clean_site_text

CHROME_EDGE = {"chrome.exe", "msedge.exe"}
FIREFOX = {"firefox.exe"}

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_cache: dict[tuple, "SiteInfo"] = {}

# 历史库查询节流：浏览器运行时 History 常被独占锁，一次查询可能阻塞数百毫秒，
# 限制同一浏览器进程的查询频率，避免 GUI 刷新线程被反复拖住
_last_db_query: dict[str, float] = {}
_DB_QUERY_MIN_INTERVAL = 3.0


def _db_query_due(proc: str) -> bool:
    now = time.monotonic()
    last = _last_db_query.get(proc, 0.0)
    if now - last >= _DB_QUERY_MIN_INTERVAL:
        _last_db_query[proc] = now
        return True
    return False


class SiteInfo:
    __slots__ = ("site", "title", "url")

    def __init__(self, site: str = "", title: str = "", url: str = ""):
        self.site = site          # 站点 key（域名）
        self.title = title        # 页面标题
        self.url = url            # 完整 URL

    def __bool__(self):
        return bool(self.site)


def _normalize(text: str) -> str:
    # 统一清洗：去掉“和另外 N 个页面”、浏览器名/个人后缀、零宽字符
    return clean_site_text(text).lower()


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _copy_readable(src: Path) -> str:
    """把可能被浏览器占用的历史库复制到临时文件，避免文件锁。"""
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        return tmp
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ""


def _open_ro(path: Path):
    """优先直接只读打开数据库，避免复制大文件；失败返回 None。"""
    try:
        # timeout=0.2：浏览器运行时会独占锁住 History 文件，sqlite 默认
        # busy 超时 5 秒会让 GUI 主线程每 2 秒卡死一次（界面“冻结”），
        # 这里用短超时快速放弃，走标题兜底
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
    except Exception:
        return None


def _now_chromium_micros() -> int:
    return int((datetime.now(timezone.utc) - _CHROME_EPOCH).total_seconds() * 1_000_000)


def _now_firefox_micros() -> int:
    return int((datetime.now(timezone.utc) - _UNIX_EPOCH).total_seconds() * 1_000_000)


def _history_candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    found: list[Path] = []
    for base in (local / "Google" / "Chrome" / "User Data",
                 local / "Microsoft" / "Edge" / "User Data"):
        try:
            base_ok = base.exists()
        except OSError:
            continue  # 目录被锁/无权限时跳过，避免采集线程崩溃
        if not base_ok:
            continue
        profiles = [base / "Default"]
        try:
            extra = sorted(base.glob("Profile*"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            extra = []
        profiles += extra
        for profile in profiles:
            h = profile / "History"
            try:
                ok = h.exists()
            except OSError:
                ok = False
            if ok and h not in found:
                found.append(h)
    return found


def _firefox_places() -> list[Path]:
    base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Profiles"
    try:
        profiles = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    result = []
    for p in profiles:
        try:
            if (p / "places.sqlite").exists():
                result.append(p / "places.sqlite")
        except OSError:
            continue
    return result


def _match_rows(rows, window_title: str) -> SiteInfo | None:
    want = _normalize(window_title)
    if not want:
        return None
    for url, title, _ in rows:
        if not url or url.startswith((
                "chrome://", "edge://", "about:", "chrome-extension://",
                "edge-extension://", "view-source:", "devtools://")):
            continue
        got = _normalize(title)
        if not got:
            continue
        if got == want or want in got or got in want:
            domain = _domain_of(url) or got
            return SiteInfo(site=domain, title=title, url=url)
    return None


def _resolve_chromium(window_title: str) -> SiteInfo | None:
    since = _now_chromium_micros() - 20 * 60 * 1_000_000
    for history in _history_candidates():
        conn = _open_ro(history)
        tmp = ""
        if conn is None:
            tmp = _copy_readable(history)
            if not tmp:
                continue
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=0.2)
        try:
            rows = conn.execute(
                "SELECT u.url, u.title, v.visit_time FROM urls u "
                "JOIN visits v ON v.url = u.id "
                "WHERE v.visit_time >= ? ORDER BY v.visit_time DESC LIMIT 300",
                (since,),
            ).fetchall()
            info = _match_rows(rows, window_title)
            if info:
                return info
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return None


def _resolve_firefox(window_title: str) -> SiteInfo | None:
    since = _now_firefox_micros() - 20 * 60 * 1_000_000
    for places in _firefox_places():
        conn = _open_ro(places)
        tmp = ""
        if conn is None:
            tmp = _copy_readable(places)
            if not tmp:
                continue
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True, timeout=0.2)
        try:
            rows = conn.execute(
                "SELECT p.url, p.title, h.visit_date FROM moz_places p "
                "JOIN moz_historyvisits h ON h.place_id = p.id "
                "WHERE h.visit_date >= ? ORDER BY h.visit_date DESC LIMIT 300",
                (since,),
            ).fetchall()
            info = _match_rows(rows, window_title)
            if info:
                return info
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    return None


def resolve_site(process_name: str, window_title: str) -> SiteInfo:
    """根据浏览器进程名 + 窗口标题解析当前网站；匹配不到时用标题兜底。"""
    proc = (process_name or "").lower()
    key = (proc, window_title or "")
    if key in _cache:
        return _cache[key]

    if not window_title or not window_title.strip():
        result = SiteInfo()
    elif proc in CHROME_EDGE and _db_query_due(proc):
        result = _resolve_chromium(window_title)
    elif proc in FIREFOX and _db_query_due(proc):
        result = _resolve_firefox(window_title)
    else:
        result = None

    if not result:
        # 匹配不到（例如隐私模式、历史被清空）：退化为用窗口标题当站点
        fallback = _normalize(window_title)[:40] or "网页"
        result = SiteInfo(site=fallback, title=window_title, url="")
    _cache[key] = result
    return result
