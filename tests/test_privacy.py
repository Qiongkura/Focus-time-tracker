"""URL 落库策略与历史保留。"""
from __future__ import annotations

from datetime import datetime, timedelta

from tracker.db import UsageDB
from tracker.privacy import UrlPolicy, apply_retention


# ---------- UrlPolicy ----------

def test_query_is_stripped_by_default():
    assert UrlPolicy().apply("https://example.com/search?q=private") == "https://example.com/search"


def test_fragment_is_stripped():
    assert UrlPolicy().apply("https://example.com/doc#section-3") == "https://example.com/doc"


def test_domain_only_when_full_url_disabled():
    policy = UrlPolicy(store_full_url=False)
    assert policy.apply("https://example.com/a/b?c=d#e") == "https://example.com"


def test_url_kept_verbatim_when_nothing_enabled():
    policy = UrlPolicy(store_full_url=True, strip_query=False)
    raw = "https://example.com/search?q=private#top"
    assert policy.apply(raw) == raw


def test_empty_url_stays_empty():
    assert UrlPolicy().apply("") == ""
    assert UrlPolicy(store_full_url=False).apply("   ") == ""


def test_non_url_text_is_dropped_when_domain_only():
    # 标题兜底会把标题写进 url 字段，此时它不是标准 URL
    assert UrlPolicy(store_full_url=False).apply("某个页面标题") == ""
    assert UrlPolicy().apply("某个页面标题") == "某个页面标题"


def test_port_and_subdomain_are_kept():
    assert UrlPolicy().apply("http://localhost:8080/x?y=1") == "http://localhost:8080/x"


def test_from_config_reads_flags():
    policy = UrlPolicy.from_config({"store_full_url": False, "strip_url_query": False})
    assert policy.store_full_url is False
    assert policy.strip_query is False

    default = UrlPolicy.from_config({})
    assert default.store_full_url is True
    assert default.strip_query is True


# ---------- 与数据库打通 ----------

def test_add_session_applies_url_policy(tmp_path):
    policy = UrlPolicy(store_full_url=True, strip_query=True)
    with UsageDB(tmp_path / "usage.db", url_policy=policy) as db:
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        db.add_session(now, now + timedelta(seconds=30),
                       process="chrome.exe", category="网站",
                       site="example.com", url="https://example.com/search?q=private#x")
        rows = db.recent_sessions_between(now - timedelta(hours=1), now + timedelta(hours=1))
    assert rows[0]["url"] == "https://example.com/search"


def test_database_defaults_to_stripping_query(tmp_path):
    """不传策略时也必须默认剥离 query——隐私保护不能靠调用方记得传参。"""
    with UsageDB(tmp_path / "usage.db") as db:
        now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        db.add_session(now, now + timedelta(seconds=30),
                       process="chrome.exe", url="https://example.com/a?token=secret")
        rows = db.recent_sessions_between(now - timedelta(hours=1), now + timedelta(hours=1))
    assert rows[0]["url"] == "https://example.com/a"


# ---------- 保留策略 ----------

def test_apply_retention_removes_old_rows(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with UsageDB(tmp_path / "usage.db") as db:
        old = now - timedelta(days=200)
        db.add_session(old, old + timedelta(seconds=60), process="old.exe")
        db.add_session(now, now + timedelta(seconds=60), process="new.exe")

        removed = apply_retention(db, 30)

        assert removed == 1
        assert db.count_sessions() == 1


def test_apply_retention_zero_means_keep_forever(tmp_path):
    now = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    with UsageDB(tmp_path / "usage.db") as db:
        old = now - timedelta(days=999)
        db.add_session(old, old + timedelta(seconds=60), process="old.exe")
        assert apply_retention(db, 0) == 0
        assert apply_retention(db, -5) == 0
        assert db.count_sessions() == 1


def test_apply_retention_tolerates_bad_value(tmp_path):
    with UsageDB(tmp_path / "usage.db") as db:
        assert apply_retention(db, "abc") == 0
        assert apply_retention(db, None) == 0
