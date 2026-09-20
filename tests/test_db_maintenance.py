"""数据库维护：schema 版本、按需回填、聚合分类、清理与重叠检查。"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tracker import db as db_module
from tracker.db import UsageDB


def _db(tmp_path: Path) -> UsageDB:
    return UsageDB(tmp_path / "usage.db")


# ---------- schema 版本与回填策略 ----------

def test_schema_version_is_recorded(tmp_path):
    with _db(tmp_path) as db:
        assert db._get_meta_int("schema_version", 0) == db_module._SCHEMA_VERSION


def test_reclassify_runs_once_on_first_open(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "project_root", lambda: tmp_path)
    calls = {"n": 0}
    original = UsageDB._backfill_games

    def counting(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(UsageDB, "_backfill_games", counting)

    db_path = tmp_path / "usage.db"
    with UsageDB(db_path):
        pass
    assert calls["n"] == 1          # 首次建库：全量回填一次


def test_reclassify_is_skipped_on_second_startup(tmp_path, monkeypatch):
    """第二次打开数据库时不应再全表回填——这是启动变慢的主因。"""
    monkeypatch.setattr(db_module, "project_root", lambda: tmp_path)
    calls = {"n": 0}
    original = UsageDB._backfill_games

    def counting(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(UsageDB, "_backfill_games", counting)

    db_path = tmp_path / "usage.db"
    with UsageDB(db_path):
        pass
    calls["n"] = 0

    with UsageDB(db_path):
        pass
    assert calls["n"] == 0          # 常规启动：完全跳过


def test_rule_file_change_triggers_reclassify(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "project_root", lambda: tmp_path)
    rules = tmp_path / "game_rules.json"
    rules.write_text('{"process_exact": ["x.exe"]}', encoding="utf-8")

    calls = {"n": 0}
    original = UsageDB._backfill_games

    def counting(self):
        calls["n"] += 1
        return original(self)

    monkeypatch.setattr(UsageDB, "_backfill_games", counting)

    db_path = tmp_path / "usage.db"
    with UsageDB(db_path):
        pass
    assert calls["n"] == 1

    calls["n"] = 0
    with UsageDB(db_path):
        pass
    assert calls["n"] == 0          # 文件没变 → 跳过

    # 内容长度也变化，避免文件系统 mtime 精度导致指纹相同
    rules.write_text('{"process_exact": ["yy.exe", "zz.exe"]}', encoding="utf-8")
    calls["n"] = 0
    with UsageDB(db_path):
        pass
    assert calls["n"] == 1          # 文件变了 → 重新分类


# ---------- 聚合分类语义 ----------

def test_desktop_summary_uses_longest_category(tmp_path):
    """同一进程既当过应用又当过游戏时，展示分类取时长更长的那个。

    旧实现用 MAX(category)（按字符串排序），会稳定地选中「游戏」——
    即使该进程绝大部分时间是在当应用使用。
    """
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(base, base + timedelta(seconds=60),
                       process="weird.exe", category="游戏")
        db.add_session(base + timedelta(minutes=1), base + timedelta(minutes=5),
                       process="weird.exe", category="应用")

        rows = db.desktop_summary_between(base.replace(hour=0), base + timedelta(hours=2))

    assert len(rows) == 1
    assert rows[0]["process"] == "weird.exe"
    assert rows[0]["seconds"] == 60 + 240      # 总时长仍包含两类
    assert rows[0]["category"] == "应用"        # 展示时长更长的那一类


def test_desktop_summary_keeps_single_category_processes(tmp_path):
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(base, base + timedelta(seconds=30), process="app.exe", category="应用")
        db.add_session(base, base + timedelta(seconds=90), process="game.exe", category="游戏")
        rows = db.desktop_summary_between(base.replace(hour=0), base + timedelta(hours=2))

    by_proc = {r["process"]: r for r in rows}
    assert by_proc["app.exe"]["category"] == "应用"
    assert by_proc["game.exe"]["category"] == "游戏"
    assert rows[0]["process"] == "game.exe"    # 按时长降序


# ---------- 数据保留 ----------

def test_purge_before_removes_only_old_rows(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    old = now - timedelta(days=200)
    with _db(tmp_path) as db:
        db.add_session(old, old + timedelta(seconds=60), process="old.exe")
        db.add_session(now, now + timedelta(seconds=60), process="new.exe")
        assert db.count_sessions() == 2

        removed = db.purge_sessions_before(now - timedelta(days=30))

        assert removed == 1
        assert db.count_sessions() == 1
        remaining = db.summary_between(now - timedelta(days=1), now + timedelta(days=1))
        assert [r["process"] for r in remaining] == ["new.exe"]


def test_purge_all_clears_everything(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(now, now + timedelta(seconds=60), process="a.exe")
        db.add_session(now, now + timedelta(seconds=60), process="b.exe")
        assert db.purge_all_sessions() == 2
        assert db.count_sessions() == 0


def test_vacuum_does_not_raise(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(now, now + timedelta(seconds=10), process="a.exe")
        db.purge_all_sessions()
        db.vacuum()
        assert db.count_sessions() == 0


# ---------- 重叠检查 ----------

def test_find_overlapping_sessions_detects_pair(tmp_path):
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(base, base + timedelta(minutes=10), process="a.exe")
        db.add_session(base + timedelta(minutes=5), base + timedelta(minutes=15),
                       process="b.exe")
        db.add_session(base + timedelta(hours=1), base + timedelta(hours=1, minutes=5),
                       process="c.exe")
        pairs = db.find_overlapping_sessions()

    assert len(pairs) == 1
    assert {pairs[0]["a_process"], pairs[0]["b_process"]} == {"a.exe", "b.exe"}


def test_find_overlapping_respects_since(tmp_path):
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        db.add_session(base, base + timedelta(minutes=10), process="a.exe")
        db.add_session(base + timedelta(minutes=5), base + timedelta(minutes=15),
                       process="b.exe")
        # 只看更晚的记录 → 这对重叠被排除在范围外
        assert db.find_overlapping_sessions(since=base + timedelta(minutes=30)) == []
        # 放宽到重叠发生之前 → 能查出来
        assert len(db.find_overlapping_sessions(since=base - timedelta(minutes=1))) == 1


def test_find_overlapping_respects_limit(tmp_path):
    base = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with _db(tmp_path) as db:
        for i in range(4):
            offset = timedelta(minutes=i)
            db.add_session(base + offset, base + offset + timedelta(minutes=10),
                           process=f"p{i}.exe")
        assert len(db.find_overlapping_sessions(limit=2)) == 2
