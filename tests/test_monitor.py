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


def _cfg(**overrides) -> dict:
    cfg = {
        "poll_interval_seconds": 0.2,
        "min_session_seconds": 0,
        "checkpoint_seconds": 60,
        "exclude_processes": [],
        "browser_site_tracking": True,
    }
    cfg.update(overrides)
    return cfg


def test_exclude_processes_is_case_insensitive(monkeypatch):
    """配置写 Chrome.exe、Windows 返回 chrome.exe，必须仍然被排除。"""
    db = _FakeDB()
    calls = {"n": 0}

    def fake_info():
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt      # 采样一次后结束循环
        return {"process": "chrome.exe", "exe_path": "", "title": "t",
                "category": "应用", "site": "", "url": "", "site_title": ""}

    monkeypatch.setattr(mon, "get_foreground_info", fake_info)
    mon.run_tracking(db, _cfg(exclude_processes=["Chrome.exe"]))
    assert db.rows == []


def test_exclude_processes_tolerates_whitespace(monkeypatch):
    db = _FakeDB()
    calls = {"n": 0}

    def fake_info():
        calls["n"] += 1
        if calls["n"] > 1:
            raise KeyboardInterrupt
        return {"process": "chrome.exe", "exe_path": "", "title": "t",
                "category": "应用", "site": "", "url": "", "site_title": ""}

    monkeypatch.setattr(mon, "get_foreground_info", fake_info)
    mon.run_tracking(db, _cfg(exclude_processes=["  CHROME.EXE  "]))
    assert db.rows == []


def test_run_tracking_stops_without_waiting_full_interval(monkeypatch):
    """stop_event 置位后应立即退出，而不是等满一个采样间隔。"""
    import threading
    import time as _time

    db = _FakeDB()
    stop = threading.Event()
    monkeypatch.setattr(mon, "get_foreground_info", lambda: None)

    # 采样间隔故意设成 30 秒：如果实现里是 sleep(interval)，这里必然超时
    cfg = _cfg(poll_interval_seconds=30)
    thread = threading.Thread(target=mon.run_tracking, args=(db, cfg, stop), daemon=True)
    start = _time.monotonic()
    thread.start()
    _time.sleep(0.2)
    stop.set()
    thread.join(timeout=5)
    elapsed = _time.monotonic() - start

    assert not thread.is_alive(), "stop_event 置位后采集线程没有退出"
    assert elapsed < 5

