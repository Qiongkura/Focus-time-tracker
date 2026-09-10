"""SQLite 存储与聚合单测（临时库）。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tracker.db import UsageDB, _split_by_day


def test_split_by_day_midnight():
    start = datetime(2026, 1, 1, 23, 0, 0)
    end = datetime(2026, 1, 2, 1, 0, 0)
    parts = _split_by_day(start, end)
    assert len(parts) == 2
    assert parts[0][0] == start
    assert parts[0][1] == datetime(2026, 1, 2, 0, 0, 0)
    assert parts[1][1] == end


def test_add_session_and_summary(tmp_path: Path):
    db_path = tmp_path / "usage.db"
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with UsageDB(db_path) as db:
        db.add_session(now, now + timedelta(seconds=120),
                       process="chrome.exe", category="应用",
                       site="example.com", url="https://example.com")
        # 跨天会话应被拆分
        late = now.replace(hour=23, minute=50)
        db.add_session(late, late + timedelta(minutes=20),
                       process="game.exe", category="游戏")
        summary = db.summary_between(now.replace(hour=0), now + timedelta(days=1, hours=1))
        by_proc = {d["process"]: d["seconds"] for d in summary}
        assert by_proc["chrome.exe"] == 120
        assert by_proc["game.exe"] == 20 * 60
        cats = db.category_summary_between(now.replace(hour=0), now + timedelta(days=1, hours=1))
        assert cats["应用"]["seconds"] == 120
        assert cats["游戏"]["seconds"] == 20 * 60


def test_sites_summary_merges_domain(tmp_path: Path):
    db_path = tmp_path / "usage.db"
    now = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
    with UsageDB(db_path) as db:
        db.add_session(now, now + timedelta(seconds=60),
                       process="chrome.exe", category="网站",
                       site="www.bilibili.com", url="https://www.bilibili.com/a")
        db.add_session(now + timedelta(minutes=1), now + timedelta(minutes=2),
                       process="chrome.exe", category="网站",
                       site="bilibili.com", url="https://bilibili.com/b")
        sites = db.sites_summary_between(now.replace(hour=0), now + timedelta(hours=5))
        assert len(sites) == 1
        assert sites[0]["site"] == "bilibili.com"
        assert sites[0]["seconds"] == 120
