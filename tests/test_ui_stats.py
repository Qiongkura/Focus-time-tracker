"""统计页拆分后的契约测试。

`app.py` 的统计页已抽到 `tracker/ui/stats_page.py`，由 `StatsPageMixin`
提供。这里锁住两件事：

1. mixin 契约——`ScreenTimeApp` 必须仍然暴露那批 `_stats_*` 方法，
   否则 `show_page` / `_on_root_resize` / `_warm_stats` 这些外部调用点会断；
2. `_hourly_usage` 的分桶纯逻辑——它只依赖 `self.db.conn.execute`，
   可以脱离 Tk 单独验证跨小时切分与分类累加。
"""
from __future__ import annotations

from datetime import datetime

import matplotlib

matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402  (需在 use("Agg") 之后)

from tracker.app import ScreenTimeApp  # noqa: E402
from tracker.ui import StatsPageMixin  # noqa: E402
from tracker.ui.stats_page import _ensure_roundtop_style  # noqa: E402

STATS_METHODS = [
    "_build_stats",
    "_stats_on_configure",
    "_schedule_stats_draw",
    "_ensure_stats_canvases",
    "_on_stats_canvas_map",
    "_draw_stats",
    "_draw_stats_impl",
    "_hourly_usage",
    "_draw_hourly",
    "_draw_week",
    "_add_bar_segment",
]


# ---------- 假件 ----------

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_params = None

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        return _FakeCursor(self._rows)


class _FakeApp(StatsPageMixin):
    def __init__(self, rows):
        self.db = type("DB", (), {"conn": _FakeConn(rows)})()


# ---------- mixin 契约 ----------

def test_app_exposes_every_stats_method():
    missing = [name for name in STATS_METHODS if not hasattr(ScreenTimeApp, name)]
    assert missing == [], f"拆分后丢失方法：{missing}"


def test_stats_mixin_is_in_mro():
    assert StatsPageMixin in ScreenTimeApp.__mro__


def test_stats_methods_are_defined_by_mixin_not_app():
    """方法应定义在 mixin 上，app.py 不再重复实现（否则就是复制而非搬迁）。"""
    assert "_build_stats" in vars(StatsPageMixin)
    assert "_add_bar_segment" in vars(StatsPageMixin)


# ---------- 圆角 boxstyle ----------

def test_roundtop_styles_are_registered():
    from matplotlib.patches import BoxStyle

    _ensure_roundtop_style()
    assert "roundtop" in BoxStyle._style_list
    assert "roundbottom" in BoxStyle._style_list


def test_add_bar_segment_picks_expected_boxstyle():
    """整柱 / 仅顶 / 仅底 / 内部直角 四种情况各走一条分支。"""
    app = _FakeApp([])
    fig = Figure(figsize=(6, 3), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 24)
    ax.set_ylim(0, 100)

    cases = [
        ((True, True), "Round"),
        ((True, False), "_RoundTop"),
        ((False, True), "_RoundBottom"),
        ((False, False), "Square"),
    ]
    for (top, bottom), expected in cases:
        before = len(ax.patches)
        app._add_bar_segment(ax, 3, 0, 0.7, 40, "#FF9F0A", top=top, bottom=bottom)
        assert len(ax.patches) == before + 1
        actual = type(ax.patches[before].get_boxstyle()).__name__
        assert actual == expected, f"top={top} bottom={bottom} -> {actual}"


def test_add_bar_segment_skips_negligible_height():
    app = _FakeApp([])
    fig = Figure(figsize=(6, 3), dpi=100)
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 24)
    ax.set_ylim(0, 100)

    app._add_bar_segment(ax, 5, 0, 0.7, 0.001, "#000000")
    assert len(ax.patches) == 0


# ---------- _hourly_usage 分桶 ----------

def test_hourly_usage_splits_across_hour_boundary():
    """10:30–11:15 应拆成 10 点 30 分钟 + 11 点 15 分钟。"""
    rows = [("应用", "2026-09-20T10:30:00", "2026-09-20T11:15:00")]
    app = _FakeApp(rows)

    buckets = app._hourly_usage(datetime(2026, 9, 20, 0, 0, 0),
                                datetime(2026, 9, 20, 23, 59, 59))

    assert len(buckets) == 24
    assert buckets[10]["应用"] == 30 * 60
    assert buckets[11]["应用"] == 15 * 60
    assert all("应用" not in buckets[h] for h in (9, 12))


def test_hourly_usage_accumulates_categories_in_same_hour():
    rows = [
        ("应用", "2026-09-20T09:00:00", "2026-09-20T09:10:00"),
        ("网站", "2026-09-20T09:20:00", "2026-09-20T09:30:00"),
        ("应用", "2026-09-20T09:40:00", "2026-09-20T09:45:00"),
    ]
    app = _FakeApp(rows)

    buckets = app._hourly_usage(datetime(2026, 9, 20, 0, 0, 0),
                                datetime(2026, 9, 20, 23, 59, 59))

    assert buckets[9]["应用"] == 15 * 60
    assert buckets[9]["网站"] == 10 * 60


def test_hourly_usage_queries_with_iso_second_precision():
    app = _FakeApp([])

    app._hourly_usage(datetime(2026, 9, 20, 1, 2, 3),
                      datetime(2026, 9, 20, 4, 5, 6))

    assert app.db.conn.last_params == ("2026-09-20T01:02:03", "2026-09-20T04:05:06")


def test_hourly_usage_returns_empty_buckets_without_rows():
    app = _FakeApp([])

    buckets = app._hourly_usage(datetime(2026, 9, 20, 0, 0, 0),
                                datetime(2026, 9, 20, 23, 59, 59))

    assert buckets == [{} for _ in range(24)]
