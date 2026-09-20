"""窗口缩放相关行为的回归测试。

覆盖两类问题：

1. **缩到最小尺寸时内容被裁**——首页双卡片竖排时没有清掉横向模式遗留的
   列配置，卡片只拿到一半宽度，行内容比卡片宽，右侧时长被画布无声裁掉。
2. **拖动时「时间条适配太慢」**——每次 ``<Configure>`` 都触发一次完整刷新，
   而一次刷新要跑二十来条 SQL；事件积压起来就表现为跟手很慢。

以及为修复后者引入的 ``ScrollArea`` 内容宽度「量化 + 限流 + 停手精确落位」
机制本身的行为。
"""
from __future__ import annotations

import time

import pytest

from tracker import theme

tk = pytest.importorskip("tkinter", reason="需要 tkinter")

from tracker.widgets import (  # noqa: E402
    ScrollArea, _ALL_SCROLL_AREAS, force_all_content_resize,
)

_ROOT_ATTEMPTS = 2


def _mapped_areas():
    out = []
    for area in list(_ALL_SCROLL_AREAS):
        try:
            if area.winfo_exists() and area.winfo_ismapped():
                out.append(area)
        except tk.TclError:
            continue
    return out


@pytest.fixture(scope="module")
def bare_root():
    """只放 ScrollArea 的裸窗口：测滚动容器的节流逻辑。"""
    last_error = None
    root = None
    for _ in range(_ROOT_ATTEMPTS):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:  # pragma: no cover - 无显示环境
            last_error = exc
            time.sleep(0.2)
    if root is None:  # pragma: no cover
        pytest.skip(f"无法创建 Tk 窗口：{last_error}")
    root.geometry("900x600")
    root.update()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def _make_area(root, **kw):
    area = ScrollArea(root, bg=theme.BG, min_width=380, respect_req=False, **kw)
    area.pack(fill="both", expand=True)
    tk.Label(area.inner, text="占位", bg=theme.BG).pack(fill="x")
    root.update()
    return area


# ---------- ScrollArea：量化 ----------

def test_first_layout_applies_immediately(bare_root):
    area = _make_area(bare_root)
    assert area._applied_content_width is not None
    assert area._content_apply_timer is None


def test_tiny_width_change_is_skipped(bare_root):
    """宽度只差不到一个量化步长：不值得为它重绘整块内容区。"""
    area = _make_area(bare_root)
    applied = area._applied_content_width
    step = int(theme.scale(ScrollArea.CONTENT_STEP))
    area._resize_content(applied + max(1, step // 2))
    assert area._applied_content_width == applied
    assert area._content_apply_timer is None
    area.destroy()


def test_large_width_change_applies(bare_root):
    area = _make_area(bare_root)
    applied = area._applied_content_width
    step = int(theme.scale(ScrollArea.CONTENT_STEP))
    area._last_content_apply = 0.0  # 解除限流，单独验证量化这一层
    area._resize_content(applied + step * 4)
    assert area._applied_content_width == applied + step * 4
    area.destroy()


# ---------- ScrollArea：限流 ----------

def test_rapid_changes_are_throttled(bare_root):
    """紧接着的第二次大变化不应立刻重排，而是排队等节流窗口。"""
    area = _make_area(bare_root)
    step = int(theme.scale(ScrollArea.CONTENT_STEP))
    area._last_content_apply = 0.0
    area._resize_content(area._applied_content_width + step * 4)
    first = area._applied_content_width

    area._resize_content(first + step * 4)  # 立刻再来一次
    assert area._applied_content_width == first, "限流失效：第二次重排被立刻执行了"
    assert area._content_apply_timer is not None, "应排队一次延迟落位"
    assert area._wanted_content_width == first + step * 4
    area.destroy()


def test_queued_apply_lands_after_interval(bare_root):
    """被限流排队的落位，会在节流窗口结束后真正执行。"""
    area = _make_area(bare_root)
    bare_root.geometry("1400x600")
    bare_root.update()
    target = max(area.canvas.winfo_width(), int(area._min_width))

    area._last_content_apply = time.monotonic()  # 限流生效中
    area._applied_content_width = 100            # 人为造一个陈旧值
    area._resize_content(target)
    assert area._applied_content_width == 100, "被限流时不该立刻重排"
    assert area._content_apply_timer is not None

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and area._applied_content_width != target:
        bare_root.update()
        time.sleep(0.01)
    assert area._applied_content_width == target
    area.destroy()


def test_reset_content_throttle_unblocks_next_layout(bare_root):
    """切页 / 重新映射后，下一次布局不该再等节流窗口。"""
    area = _make_area(bare_root)
    area._last_content_apply = time.monotonic()
    area._reset_content_throttle()
    assert area._last_content_apply == 0.0
    area.destroy()


# ---------- ScrollArea：精确落位 ----------

def test_force_resize_content_applies_exactly(bare_root):
    area = _make_area(bare_root)
    bare_root.geometry("1100x600")
    bare_root.update()
    area._content_apply_timer = None
    area._applied_content_width = 12345  # 人为造一个陈旧值
    area.force_resize_content()

    expected = max(area.canvas.winfo_width(), int(area._min_width))
    assert area._applied_content_width == expected
    assert area._content_apply_timer is None
    # 解开限流：紧随其后的滚动条显隐要能立刻生效
    assert area._last_content_apply == 0.0
    area.destroy()


def test_force_resize_cancels_queued_apply(bare_root):
    area = _make_area(bare_root)
    step = int(theme.scale(ScrollArea.CONTENT_STEP))
    area._last_content_apply = 0.0
    area._resize_content(area._applied_content_width + step * 4)
    area._resize_content(area._applied_content_width + step * 8)
    assert area._content_apply_timer is not None

    area.force_resize_content()
    assert area._content_apply_timer is None, "强制落位后不该再留着排队定时器"
    area.destroy()


def test_force_all_skips_unmapped_areas(bare_root):
    mapped = _make_area(bare_root)
    unmapped = ScrollArea(bare_root, bg=theme.BG, min_width=380, respect_req=False)
    unmapped._applied_content_width = 424242  # 没 pack -> 未映射

    force_all_content_resize()

    assert unmapped._applied_content_width == 424242, "未映射的容器不该被重排"
    assert mapped._applied_content_width == max(mapped.canvas.winfo_width(),
                                                int(mapped._min_width))
    unmapped.destroy()
    mapped.destroy()


# ---------------------------------------------------------------------------
# 应用级：真窗口
# ---------------------------------------------------------------------------

from tracker import app as app_module  # noqa: E402
from tracker.config import DEFAULTS  # noqa: E402
from tracker.db import UsageDB  # noqa: E402
from tracker.demo import seed_demo_data  # noqa: E402

PAGES = ["home", "stats", "records", "categories", "settings"]


@pytest.fixture
def gui_app(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib", reason="统计页需要 matplotlib")
    monkeypatch.setattr(app_module.ScreenTimeApp, "_spawn_background", lambda self: None)
    monkeypatch.setattr(app_module.ScreenTimeApp, "_setup_tray", lambda self: None)

    last_error = None
    app = None
    for _ in range(_ROOT_ATTEMPTS):
        try:
            db = UsageDB(tmp_path / "usage.db")
            seed_demo_data(db, days=2)
            cfg = dict(DEFAULTS)
            cfg["data_dir"] = str(tmp_path / "data")
            app = app_module.ScreenTimeApp(db, cfg, tmp_path / "reports")
            break
        except tk.TclError as exc:  # pragma: no cover - 无显示环境
            last_error = exc
            time.sleep(0.2)
    if app is None:  # pragma: no cover
        pytest.skip(f"无法创建 Tk 窗口：{last_error}")
    try:
        yield app
    finally:
        try:
            app._preview.stop()
            app.root.destroy()
        except tk.TclError:
            pass


def _settle(app, size=None, frames=12):
    if size:
        app.root.geometry(size)
    for _ in range(frames):
        app.root.update()
    app.root.update_idletasks()


def _min_size(app):
    return f"{int(760 * app._dpi)}x{int(560 * app._dpi)}"


def test_min_window_shows_home_rows_fully(gui_app):
    """缩到最小尺寸时，卡片里的行内容不能被画布裁掉（时间条/时长要看得见）。"""
    app = gui_app
    app.show_page("home")
    _settle(app, _min_size(app), frames=16)
    app._on_content_settled()
    _settle(app, frames=4)

    checked = 0
    for key in ("应用", "网站"):
        card = app._card_widgets.get(key)
        if card is None:
            continue
        cw = card.winfo_width()
        for info in app._card_rows.get(key) or []:
            bbox = card.bbox(info["win"])
            if not bbox:
                continue
            checked += 1
            assert bbox[2] <= cw, f"{key} 卡片行内容超出卡片：{bbox[2]} > {cw}"
    assert checked > 0, "没有可检查的行（示例数据没生成？）"


def test_stack_mode_card_takes_full_width(gui_app):
    """竖排时卡片必须拿全宽，而不是被横向模式遗留的列配置挤成一半。"""
    app = gui_app
    app.show_page("home")
    _settle(app, _min_size(app), frames=16)
    cards = app.app_card.master
    container_w = cards.winfo_width()
    assert app._home_card_mode == "stack", "最小宽度下首页卡片应该是竖排"
    assert app.app_card.winfo_width() >= container_w * 0.85, \
        "竖排时卡片没拿到全宽（列配置没重置？）"


def test_relayout_home_cards_is_idempotent(gui_app):
    """模式没变时不该再动 grid——否则每次缩放都会引发 <Configure> 级联重绘。"""
    app = gui_app
    app.show_page("home")
    _settle(app, frames=12)
    app._relayout_home_cards()  # 先让模式确定下来

    calls = []

    def spy(*_a, **_k):
        calls.append(1)

    for c in (app.app_card, app.site_card):
        c.grid_forget = spy
    app._relayout_home_cards()
    assert calls == [], "模式没变却动了 grid"


def test_resize_path_never_runs_full_refresh(gui_app, monkeypatch):
    """缩放只做布局：不能顺手跑完整刷新（那要跑二十来条 SQL）。"""
    app = gui_app
    calls = []
    monkeypatch.setattr(app, "refresh", lambda: calls.append(1))

    app._on_root_resize()
    app._on_resize_settled()
    app._on_content_settled()

    assert calls == [], "缩放路径上跑了完整刷新"


def test_content_settled_forces_exact_content_width(gui_app):
    """停手后必须把内容宽度精确落位，不能停在量化残差上。"""
    app = gui_app
    app.show_page("home")
    _settle(app, _min_size(app), frames=16)

    areas = _mapped_areas()
    assert areas, "没有可见的滚动容器"
    for area in areas:
        area._applied_content_width = 987654  # 人为造一个陈旧值

    app._on_content_settled()

    for area in areas:
        expected = max(area.canvas.winfo_width(), int(area._min_width))
        assert area._applied_content_width == expected
        assert area._content_apply_timer is None


def test_all_pages_fit_at_min_window(gui_app):
    """每个页面在最小窗口下都不该需要横向滚动（内容宽度不超过画布宽度）。"""
    app = gui_app
    app.root.geometry(_min_size(app))
    for _ in range(10):
        app.root.update()

    for page in PAGES:
        app.show_page(page)
        for _ in range(14):
            app.root.update()
        app._on_content_settled()
        for _ in range(6):
            app.root.update()
        for area in _mapped_areas():
            cw = area.canvas.winfo_width()
            inner_w = area.inner.winfo_width()
            assert inner_w <= cw + 2, \
                f"{page} 页内容({inner_w}) 比画布({cw}) 宽，右侧要横向滚动才看得见"
