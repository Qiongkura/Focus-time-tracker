"""采集组件：切段、checkpoint、跨午夜、排除与停止响应。"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from tracker.db import UsageDB
from tracker.monitor import (
    SessionAccumulator, SessionWriter, TrackingService, WindowSampler,
)

BASE = datetime(2026, 1, 1, 10, 0, 0)


class FakeDB:
    def __init__(self):
        self.rows = []

    def add_session(self, start, end, process, exe_path="", title="",
                    category="应用", site="", url=""):
        self.rows.append({
            "start": start, "end": end, "process": process,
            "category": category, "site": site, "url": url,
        })


def _t(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _info(process="code.exe", category="应用", site="", title="t"):
    return {"process": process, "exe_path": "", "title": title,
            "category": category, "site": site, "site_title": title, "url": ""}


def _drain(acc: SessionAccumulator, writer: SessionWriter, info, seconds: int) -> None:
    for session, end in acc.observe(info, _t(seconds)):
        writer.write(session, end)


def _flush(acc: SessionAccumulator, writer: SessionWriter, seconds: int) -> None:
    segment = acc.flush(_t(seconds))
    if segment:
        writer.write(*segment)


def _service(db, **cfg):
    base_cfg = {
        "poll_interval_seconds": 0.05,
        "min_session_seconds": 0,
        "checkpoint_seconds": 60,
        "exclude_processes": [],
        "browser_site_tracking": True,
    }
    base_cfg.update(cfg.pop("cfg", {}))
    return TrackingService(db, base_cfg, **cfg)


# ---------- WindowSampler ----------

def test_sampler_uses_injected_collector():
    raw = {"hwnd": 1, "pid": 2, "process": "code.exe", "exe_path": "",
           "title": "t", "class_name": "", "minimized": False}
    sampler = WindowSampler(collect=lambda: raw)
    assert sampler.sample() is raw
    assert sampler.info()["process"] == "code.exe"


def test_sampler_handles_missing_window():
    sampler = WindowSampler(collect=lambda: None)
    assert sampler.sample() is None
    assert sampler.info() is None


# ---------- 时长门槛 ----------

def test_short_session_is_dropped():
    db = FakeDB()
    acc = SessionAccumulator(min_session=3)
    writer = SessionWriter(db, min_session=3)

    for second in (0, 1, 2):
        _drain(acc, writer, _info(), second)
    _flush(acc, writer, 2)

    assert db.rows == []


def test_session_meeting_threshold_is_written():
    db = FakeDB()
    acc = SessionAccumulator(min_session=3)
    writer = SessionWriter(db, min_session=3)

    for second in (0, 1, 2, 3, 4):
        _drain(acc, writer, _info(), second)
    _flush(acc, writer, 4)

    assert len(db.rows) == 1
    assert db.rows[0]["start"] == _t(0)
    assert db.rows[0]["end"] == _t(4)


# ---------- 窗口切换 ----------

def test_switching_process_closes_previous_segment():
    """应用 A 10 秒 → B 5 秒 → A 8 秒：应切成三段，时长与顺序都不能错。"""
    db = FakeDB()
    acc = SessionAccumulator(min_session=0)
    writer = SessionWriter(db, min_session=0)

    for second in (0, 2, 4, 6, 8, 10):
        _drain(acc, writer, _info("a.exe"), second)
    for second in (10, 12, 14, 15):
        _drain(acc, writer, _info("b.exe"), second)
    for second in (15, 18, 20, 23):
        _drain(acc, writer, _info("a.exe"), second)
    _flush(acc, writer, 23)

    assert [(r["process"], r["start"], r["end"]) for r in db.rows] == [
        ("a.exe", _t(0), _t(10)),
        ("b.exe", _t(10), _t(15)),
        ("a.exe", _t(15), _t(23)),
    ]


def test_switching_site_splits_browser_segment():
    db = FakeDB()
    acc = SessionAccumulator(min_session=0)
    writer = SessionWriter(db, min_session=0)

    _drain(acc, writer, _info("chrome.exe", "网站", site="a.com"), 0)
    _drain(acc, writer, _info("chrome.exe", "网站", site="b.com"), 5)
    _flush(acc, writer, 8)

    assert [(r["site"], r["start"], r["end"]) for r in db.rows] == [
        ("a.com", _t(0), _t(5)),
        ("b.com", _t(5), _t(8)),
    ]


def test_missing_window_closes_current_segment():
    db = FakeDB()
    acc = SessionAccumulator(min_session=0)
    writer = SessionWriter(db, min_session=0)

    _drain(acc, writer, _info(), 0)
    _drain(acc, writer, _info(), 4)
    _drain(acc, writer, None, 6)          # 拿不到前台窗口
    _drain(acc, writer, _info(), 8)
    _flush(acc, writer, 10)

    assert [(r["start"], r["end"]) for r in db.rows] == [
        (_t(0), _t(6)),
        (_t(8), _t(10)),
    ]


# ---------- checkpoint ----------

def test_checkpoint_segments_do_not_overlap_or_lose_time():
    """长会话按 checkpoint 落多段：段之间不重叠，总时长不丢不多。"""
    db = FakeDB()
    acc = SessionAccumulator(min_session=0, checkpoint_seconds=10)
    writer = SessionWriter(db, min_session=0)

    for second in range(0, 36):
        _drain(acc, writer, _info("a.exe"), second)
    _flush(acc, writer, 36)

    assert len(db.rows) >= 3
    for previous, following in zip(db.rows, db.rows[1:]):
        assert previous["end"] <= following["start"], "checkpoint 段出现重叠"
    assert db.rows[0]["start"] == _t(0)
    assert db.rows[-1]["end"] == _t(36)
    total = sum((r["end"] - r["start"]).total_seconds() for r in db.rows)
    assert total == 36


# ---------- 跨午夜 ----------

def test_session_crossing_midnight_is_split_by_database(tmp_path):
    # checkpoint 调大，让整段会话只在 flush 时落库，专注验证跨天拆分
    acc = SessionAccumulator(min_session=0, checkpoint_seconds=3600)
    base = datetime(2026, 1, 1, 23, 59, 30)

    with UsageDB(tmp_path / "usage.db") as db:
        writer = SessionWriter(db, min_session=0)
        for offset in (0, 30, 60, 90):
            for session, end in acc.observe(_info(), base + timedelta(seconds=offset)):
                writer.write(session, end)
        segment = acc.flush(base + timedelta(seconds=120))
        if segment:
            writer.write(*segment)

        rows = db.recent_sessions_between(base - timedelta(days=1),
                                          base + timedelta(days=1))

    assert len(rows) == 2, "跨午夜的会话应被拆成两天"
    # recent_sessions_between 按 start_time 倒序返回
    assert rows[0]["start"][:10] == "2026-01-02"     # 后一段属于新的一天
    assert rows[1]["start"][:10] == "2026-01-01"     # 前一段属于旧的一天
    assert rows[1]["end"][:10] == "2026-01-02"       # 前一段被截断在午夜


# ---------- 停止响应 ----------

def test_tracking_service_stops_without_waiting_full_interval():
    stop = threading.Event()
    service = _service(FakeDB(),
                       cfg={"poll_interval_seconds": 30},
                       stop_event=stop,
                       fetch_info=lambda: None)

    thread = threading.Thread(target=service.run, daemon=True)
    started = time.monotonic()
    thread.start()
    time.sleep(0.2)
    stop.set()
    thread.join(timeout=5)

    assert not thread.is_alive(), "stop_event 置位后采集循环没有退出"
    assert time.monotonic() - started < 5


# ---------- 排除与网站开关 ----------

def test_tracking_service_excludes_case_insensitively():
    db = FakeDB()
    service = _service(db,
                       cfg={"exclude_processes": ["Chrome.exe"]},
                       fetch_info=lambda: _info("chrome.exe"))

    for _ in range(3):
        service.tick()
    service._flush()

    assert db.rows == []


def test_tracking_service_demotes_browser_when_site_tracking_disabled():
    db = FakeDB()
    service = _service(db,
                       cfg={"browser_site_tracking": False},
                       fetch_info=lambda: _info("chrome.exe", "网站", site="bilibili.com"))

    service.tick()
    service._flush()

    assert len(db.rows) == 1
    assert db.rows[0]["category"] == "应用"
    assert db.rows[0]["site"] == ""
