"""浏览器当前网页识别。

原理：浏览器窗口标题通常包含当前页面的标题；浏览器把访问记录写入 SQLite
历史库。我们用窗口标题去历史库里匹配最近访问的页面，从而得到“正在看哪个网站”。
支持 Chrome / Edge / Brave / Vivaldi / Opera / QQ 浏览器等 Chromium 系浏览器及 Firefox 系浏览器。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from .utils import clean_site_text

CHROME_EDGE = {"chrome.exe", "msedge.exe"}
CHROMIUM_BROWSERS = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "vivaldi.exe",
    "opera.exe",
    "arc.exe",
    "qqbrowser.exe",
    "360chrome.exe",
    "360se.exe",
    "centbrowser.exe",
    "chromium.exe",
}
FIREFOX = {"firefox.exe"}
FIREFOX_BROWSERS = {"firefox.exe", "waterfox.exe", "librewolf.exe", "zen.exe"}
BROWSER_PROCESSES = CHROMIUM_BROWSERS | FIREFOX_BROWSERS

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_cache: dict[tuple, SiteInfo] = {}
_CACHE_MAX = 512

# 历史库查询节流：限制同一浏览器进程的查询频率，避免频繁触发磁盘 I/O
_last_db_query: dict[str, float] = {}
_DB_QUERY_MIN_INTERVAL = 3.0
_ctx = threading.local()


def _db_query_due(proc: str) -> bool:
    now = time.monotonic()
    last = _last_db_query.get(proc, 0.0)
    if now - last >= _DB_QUERY_MIN_INTERVAL:
        _last_db_query[proc] = now
        return True
    return False


def _cache_put(key: tuple, value: SiteInfo) -> SiteInfo:
    if len(_cache) >= _CACHE_MAX and key not in _cache:
        # 简单 FIFO 上限：先入先出丢弃，避免长会话无限涨内存
        _cache.pop(next(iter(_cache)), None)
    _cache[key] = value
    return value


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
    """把可能被浏览器占用的历史库复制到临时文件，作为只读打开失败后的最终兜底。"""
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


def _sqlite_file_uri(path: Path, query: str) -> str:
    encoded = quote(path.resolve().as_posix(), safe=":/")
    return f"file:{encoded}?{query}"


def _query_history_rows(db_path: Path, sql: str, params: tuple) -> list:
    """读取浏览器历史库：优先只读直连 → immutable 零拷贝绕锁 → 临时文件复制兜底。

    Chrome/Edge 运行时通常会独占锁住 ``History``（动辄数十上百 MB）。先尝试
    ``mode=ro&immutable=1`` 可直接从系统页缓存零拷贝读取（<1ms），避免每 3 秒向
    ``%TEMP%`` 拷贝整库；仅当恰好撞上浏览器页面写入导致校验失败时才退化到文件拷贝。
    """
    for query in ("mode=ro", "mode=ro&immutable=1"):
        conn = None
        try:
            conn = sqlite3.connect(_sqlite_file_uri(db_path, query), uri=True, timeout=0.15)
            return conn.execute(sql, params).fetchall()
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    tmp = _copy_readable(db_path)
    if not tmp:
        return []
    conn = None
    try:
        conn = sqlite3.connect(_sqlite_file_uri(Path(tmp), "mode=ro"), uri=True, timeout=0.2)
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        try:
            os.remove(tmp)
        except OSError:
            pass


def _now_chromium_micros() -> int:
    return int((datetime.now(timezone.utc) - _CHROME_EPOCH).total_seconds() * 1_000_000)


def _now_firefox_micros() -> int:
    return int((datetime.now(timezone.utc) - _UNIX_EPOCH).total_seconds() * 1_000_000)


def _chromium_user_data_dirs(proc: str) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    mapping: dict[str, list[Path]] = {
        "chrome.exe": [local / "Google" / "Chrome" / "User Data"],
        "msedge.exe": [local / "Microsoft" / "Edge" / "User Data"],
        "brave.exe": [local / "BraveSoftware" / "Brave-Browser" / "User Data"],
        "vivaldi.exe": [local / "Vivaldi" / "User Data"],
        "arc.exe": [local / "Arc" / "User Data"],
        "centbrowser.exe": [local / "CentBrowser" / "User Data"],
        "chromium.exe": [local / "Chromium" / "User Data"],
        "qqbrowser.exe": [local / "Tencent" / "QQBrowser" / "User Data"],
        "360chrome.exe": [local / "360Chrome" / "Chrome" / "User Data"],
        "360se.exe": [roaming / "360se6" / "User Data"],
        "opera.exe": [
            roaming / "Opera Software" / "Opera Stable",
            roaming / "Opera Software" / "Opera GX Stable",
        ],
    }
    if proc in mapping:
        return mapping[proc]
    return [
        local / "Google" / "Chrome" / "User Data",
        local / "Microsoft" / "Edge" / "User Data",
    ]


def _history_candidates(process_name: str = "") -> list[Path]:
    proc = (process_name or "").lower()
    found: list[Path] = []
    for base in _chromium_user_data_dirs(proc):
        try:
            base_ok = base.exists()
        except OSError:
            continue
        if not base_ok:
            continue
        try:
            extra = sorted(base.glob("Profile*"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            extra = []
        # Opera 等浏览器直接把 History 放在根目录；常规 Chromium 放在 Default / Profile*
        profiles = [base / "Default"] + extra + [base]
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
    roaming = Path(os.environ.get("APPDATA", ""))
    bases = (
        roaming / "Mozilla" / "Firefox" / "Profiles",
        roaming / "Waterfox" / "Profiles",
        roaming / "librewolf" / "Profiles",
        roaming / "zen" / "Profiles",
    )
    result: list[Path] = []
    for base in bases:
        try:
            if not base.exists():
                continue
            profiles = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for p in profiles:
            try:
                cand = p / "places.sqlite"
                if cand.exists() and cand not in result:
                    result.append(cand)
            except OSError:
                continue
    return result


def _match_rows(rows, window_title: str) -> SiteInfo | None:
    want = _normalize(window_title)
    if not want:
        return None
    for url, title, _ in rows:
        if not url or url.startswith((
                "chrome://", "edge://", "brave://", "vivaldi://", "opera://",
                "about:", "chrome-extension://", "edge-extension://",
                "view-source:", "devtools://")):
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
    proc = getattr(_ctx, "proc", "")
    sql = (
        "SELECT u.url, u.title, v.visit_time FROM urls u "
        "JOIN visits v ON v.url = u.id "
        "WHERE v.visit_time >= ? ORDER BY v.visit_time DESC LIMIT 300"
    )
    for history in _history_candidates(proc):
        rows = _query_history_rows(history, sql, (since,))
        info = _match_rows(rows, window_title)
        if info:
            return info
    return None


def _resolve_firefox(window_title: str) -> SiteInfo | None:
    since = _now_firefox_micros() - 20 * 60 * 1_000_000
    sql = (
        "SELECT p.url, p.title, h.visit_date FROM moz_places p "
        "JOIN moz_historyvisits h ON h.place_id = p.id "
        "WHERE h.visit_date >= ? ORDER BY h.visit_date DESC LIMIT 300"
    )
    for places in _firefox_places():
        rows = _query_history_rows(places, sql, (since,))
        info = _match_rows(rows, window_title)
        if info:
            return info
    return None


def resolve_site(process_name: str, window_title: str) -> SiteInfo:
    """根据浏览器进程名 + 窗口标题解析当前网站；匹配不到时用标题兜底。"""
    proc = (process_name or "").lower()
    title = window_title or ""
    key = (proc, title)
    if key in _cache:
        return _cache[key]
    if not title.strip():
        return _cache_put(key, SiteInfo())

    is_browser = proc in CHROMIUM_BROWSERS or proc in FIREFOX_BROWSERS
    if not is_browser:
        return _cache_put(key, _title_fallback(title))

    # 节流到期：真正查历史库；未到期只做临时标题兜底，不落永久缓存，
    # 下次同一标题到达时再尝试查库
    if not _db_query_due(proc):
        return _title_fallback(title)

    _ctx.proc = proc
    try:
        result = _resolve_chromium(title) if proc in CHROMIUM_BROWSERS else _resolve_firefox(title)
    finally:
        _ctx.proc = ""
    if result:
        return _cache_put(key, result)
    # 查过库仍匹配不到（隐私模式、历史被清空等）：标题兜底并缓存
    return _cache_put(key, _title_fallback(title))


def _title_fallback(title: str) -> SiteInfo:
    """匹配不到域名时退化为用窗口标题当站点（仅展示，不保证可合并）。"""
    site = _normalize(title)[:40] or "网页"
    return SiteInfo(site=site, title=title, url="")
