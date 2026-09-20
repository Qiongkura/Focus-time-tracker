"""详细记录页拆分后的契约与合并逻辑测试。

记录页已抽到 `tracker/ui/records_page.py`，由 `RecordsPageMixin` 提供。
这里锁住：

1. mixin 契约——`ScreenTimeApp` 仍暴露 4 个 `_records` 方法；
2. `group_sessions` 的合并规则——它是纯函数，可以脱离 Tk 完整验证；
3. `_build_records_chunk` 的过期批次短路——必须不碰 Tk 组件就返回。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tracker.app import ScreenTimeApp
from tracker.ui import RecordsPageMixin
from tracker.ui.records_page import RECORDS_CHUNK, group_sessions

FALLBACK = datetime(2026, 9, 20, 12, 0, 0)

RECORDS_METHODS = [
    "_build_records",
    "_refresh_records",
    "_build_record_row",
    "_build_records_chunk",
]


def _row(process, start, duration, category="应用", site="", title=""):
    try:
        end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="seconds")
    except ValueError:
        end = start
    return {"process": process, "category": category, "site": site, "title": title,
            "start": start, "end": end, "duration": duration}


# ---------- mixin 契约 ----------

def test_app_exposes_every_records_method():
    missing = [name for name in RECORDS_METHODS if not hasattr(ScreenTimeApp, name)]
    assert missing == [], f"拆分后丢失方法：{missing}"


def test_records_mixin_is_in_mro_and_owns_methods():
    assert RecordsPageMixin in ScreenTimeApp.__mro__
    assert "_build_records" in vars(RecordsPageMixin)
    assert "_refresh_records" in vars(RecordsPageMixin)


def test_chunk_size_is_twelve():
    assert RECORDS_CHUNK == 12


# ---------- 过期批次短路 ----------

class _StaleProbe(RecordsPageMixin):
    def __init__(self, build_id):
        self._records_build_id = build_id
        self.touched_inner = False

    @property
    def records_inner(self):
        self.touched_inner = True
        raise AssertionError("过期批次不应访问 Tk 组件")


def test_stale_chunk_returns_without_touching_widgets():
    probe = _StaleProbe(build_id=7)
    probe._build_records_chunk(3)
    assert probe.touched_inner is False


def test_chunk_returns_when_no_build_id_has_been_set():
    probe = _StaleProbe(build_id=-1)
    probe._build_records_chunk(0)
    assert probe.touched_inner is False


# ---------- group_sessions 合并规则 ----------

def test_same_hour_and_process_is_merged():
    rows = [
        _row("code.exe", "2026-09-20T09:05:00", 300),
        _row("code.exe", "2026-09-20T09:40:00", 120),
    ]
    out = group_sessions(rows, FALLBACK)

    assert len(out) == 1
    assert out[0]["count"] == 2
    assert out[0]["seconds"] == 420
    assert out[0]["hour"] == datetime(2026, 9, 20, 9, 0, 0)


def test_different_hours_are_not_merged():
    rows = [
        _row("code.exe", "2026-09-20T09:05:00", 300),
        _row("code.exe", "2026-09-20T10:05:00", 300),
    ]
    out = group_sessions(rows, FALLBACK)

    assert len(out) == 2


def test_websites_merge_by_site_across_processes():
    """同站点在不同浏览器里也应合并成一行。"""
    rows = [
        _row("chrome.exe", "2026-09-20T09:05:00", 300, category="网站", site="github.com"),
        _row("msedge.exe", "2026-09-20T09:30:00", 200, category="网站", site="github.com"),
    ]
    out = group_sessions(rows, FALLBACK)

    assert len(out) == 1
    assert out[0]["identity"] == "github.com"
    assert out[0]["count"] == 2
    assert out[0]["seconds"] == 500


def test_same_process_but_different_categories_stay_separate():
    rows = [
        _row("chrome.exe", "2026-09-20T09:05:00", 300, category="网站", site="a.com"),
        _row("chrome.exe", "2026-09-20T09:10:00", 300, category="应用"),
    ]
    out = group_sessions(rows, FALLBACK)

    assert len(out) == 2


def test_longest_session_wins_title_and_site():
    rows = [
        _row("chrome.exe", "2026-09-20T09:05:00", 60, category="网站",
             site="a.com", title="短标题"),
        _row("chrome.exe", "2026-09-20T09:30:00", 600, category="网站",
             site="a.com", title="长标题"),
    ]
    out = group_sessions(rows, FALLBACK)

    assert out[0]["title"] == "长标题"


def test_sorted_by_start_desc():
    rows = [
        _row("a.exe", "2026-09-20T08:00:00", 100),
        _row("b.exe", "2026-09-20T10:00:00", 100),
    ]
    out = group_sessions(rows, FALLBACK)

    assert [g["process"] for g in out] == ["b.exe", "a.exe"]


def test_tie_on_start_time_keeps_existing_order():
    """同一 start 的分组，当前实现下是「时长升序」——这是拆分前就有的行为。

    key 为 ``(start, -seconds)`` 且 ``reverse=True``，整体反转时 ``-seconds``
    也跟着反转，于是时长短的反倒排前面。原代码的注释看起来是想要时长降序，
    但实际不是；这里先固化现状，避免拆分顺带改变展示顺序。
    """
    rows = [
        _row("short.exe", "2026-09-20T10:00:00", 100),
        _row("long.exe", "2026-09-20T10:00:00", 900),
    ]
    out = group_sessions(rows, FALLBACK)

    assert [g["process"] for g in out] == ["short.exe", "long.exe"]


def test_unparsable_timestamp_falls_back_to_end():
    rows = [
        _row("code.exe", "not-a-date", 120),
    ]
    out = group_sessions(rows, FALLBACK)

    assert len(out) == 1
    assert out[0]["hour"] == FALLBACK.replace(minute=0, second=0, microsecond=0)


def test_missing_duration_treated_as_zero():
    rows = [
        _row("code.exe", "2026-09-20T09:05:00", None),
    ]
    out = group_sessions(rows, FALLBACK)

    assert out[0]["seconds"] == 0.0
    assert out[0]["count"] == 1


def test_empty_rows_give_empty_result():
    assert group_sessions([], FALLBACK) == []
