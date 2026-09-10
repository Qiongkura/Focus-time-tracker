"""分类优先级与监控工具单测（不依赖真实 Win32 前台）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from tracker.browser import SiteInfo
from tracker import monitor as mon
from tracker.monitor import _session_key, _write_segment, classify_window


class _FakeDB:
    def __init__(self):
        self.rows = []

    def add_session(self, start, end, process, exe_path="", title="",
                    category="应用", site="", url=""):
        self.rows.append({
            "start": start, "end": end, "process": process,
            "category": category, "site": site,
        })


def test_classify_browser_is_site(monkeypatch):
    monkeypatch.setattr(
        mon, "resolve_site",
        lambda _p, title: SiteInfo(site="example.com", title=title, url="https://example.com"),
    )
    info = classify_window(process="chrome.exe", exe_path=r"C:\chrome.exe",
                           title="Example - Google Chrome")
    assert info["category"] == "网站"
    assert info["site"] == "example.com"


def test_classify_minimized_is_system():
    info = classify_window(process="code.exe", title="x", minimized=True)
    assert info["category"] == "系统"
    assert info["process"] == "最小化"


def test_session_key_by_site():
    a = _session_key({"category": "网站", "site": "a.com"})
    b = _session_key({"category": "网站", "site": "b.com"})
    c = _session_key({"category": "应用", "process": "chrome.exe"})
    assert a != b
    assert a != c


def test_write_segment_skips_short():
    db = _FakeDB()
    now = datetime(2026, 1, 1, 12, 0, 0)
    current = {"process": "code.exe", "exe_path": "", "title": "t",
               "category": "应用", "site": "", "url": "", "start": now}
    _write_segment(db, current, now + timedelta(seconds=1), min_session=3)
    assert db.rows == []
    _write_segment(db, current, now + timedelta(seconds=10), min_session=3)
    assert len(db.rows) == 1
    assert db.rows[0]["process"] == "code.exe"
