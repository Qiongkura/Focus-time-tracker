"""Windows 浅色风格屏幕使用时间 GUI（只读展示，采集由后台子进程自动启动）。

这个文件现在只是**组装入口**：页面与基础设施按职责拆到 ``tracker/ui/``
下的各个 mixin，``ScreenTimeApp`` 通过多继承把它们拼成一个窗口类。
mixin 之间只通过 ``self`` 交换状态，所以拆分没有改动任何外部调用点。

拆分后的分工：

===============  ==========================================
``app_shell``    窗口骨架：DPI / 样式 / 侧边栏 / 页面容器 / 日志 / 生命周期
``home_page``    首页概览卡片与时段切换
``stats_page``   统计页（matplotlib 图表）
``records_page`` 详细记录页（按小时合并 + 分批渲染）
``categories_page`` 分类页（三栏 + 进程改判）
``settings_page``   设置页（采样参数 / 排除进程 / 隐私与数据维护）
``refresh_controller`` 刷新调度与跨 0 点提示
``background``   后台采集子进程、托盘、启动期自检
``preview``      前台窗口预览采样线程
===============  ==========================================
"""
from __future__ import annotations

import ctypes
import tkinter as tk
from pathlib import Path

from . import theme, tray
from .db import UsageDB
from .ui import (
    BackgroundMixin,
    CategoriesPageMixin,
    HomePageMixin,
    RecordsPageMixin,
    RefreshMixin,
    SettingsPageMixin,
    ShellMixin,
    StatsPageMixin,
)

# 重新导出：main.py 与测试仍按 ``from tracker.app import ...`` 取这两个名字
from .ui.app_shell import rotate_log  # noqa: F401
from .ui.preview import ForegroundPreviewWorker  # noqa: F401
from .widgets import set_icon_root

__all__ = [
    "ForegroundPreviewWorker",
    "ScreenTimeApp",
    "rotate_log",
    "run_app",
]


def _enable_high_dpi():
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# 必须在创建 Tk root 之前调用
_enable_high_dpi()


class ScreenTimeApp(
    ShellMixin,
    HomePageMixin,
    StatsPageMixin,
    RecordsPageMixin,
    CategoriesPageMixin,
    SettingsPageMixin,
    RefreshMixin,
    BackgroundMixin,
):
    """主窗口：组装各 mixin，并持有它们共享的状态。"""

    def __init__(self, db: UsageDB, cfg: dict, report_dir: Path,
                 *, start_minimized: bool = False):
        self.db = db
        self.cfg = dict(cfg)
        self.report_dir = Path(report_dir)
        self.period = "今日"
        self.filter = "应用"
        self.current_page = "home"
        self._rows_sig = {}
        self._card_rows = {}
        self._card_heights = {}
        self._card_widths = {}  # 最近一次成功布局的卡片宽度缓存（窗口未布局时兜底）
        self._card_widgets = {}  # card_key -> 卡片控件，窗口缩放时做轻量重排
        self._card_payload = {}  # card_key -> 最近一次的数据，缩放时不必重新查库
        self._card_bound = set()  # 已挂上 <Configure> 的 card_key，避免重复绑定
        self._card_applied_width = {}  # card_key -> 已排布的行内容宽度（缩放短路用）
        self._card_autosize = set()  # 高度跟随内容的卡片（分类页），首页卡片不在此列
        self._home_card_mode = None  # 首页双卡片布局模式：side / stack
        self._cat_card_mode = None  # 分类三卡布局模式：side / stack
        self._resize_timer = None  # 窗口缩放防抖定时器（布局收尾）
        self._content_timer = None  # 窗口缩放防抖定时器（内容宽度精确落位）
        self._refresh_timer = None  # 2 秒定时刷新循环
        self._boot_refresh_timer = None  # 启动后首次刷新
        self._boot_warm_timer = None  # 启动后统计图预热
        self._hourly_timer = None  # 整点统计图刷新
        self._stats_redraw_timer = None  # 统计图防抖重绘
        self._records_chunk_timer = None  # 记录页分批流式构建定时器
        self._dialog_timer = None  # 延迟弹出的提示框（关窗时要能取消）
        self._closing = False  # 关窗流程已开始：所有定时回调直接返回
        self._hidden_to_tray = False  # 隐藏到系统托盘期间暂停定时刷新与预览采样
        self._last_refresh_date = None  # 跨 0 点检测：上次刷新时的日期
        self._card_total_labels = {}
        self._period_btn_groups = []  # 跨页面同步的「今日/本周」切换按钮组
        self._db_exe_cache = {}  # 历史进程名 -> 数据库中记录的 exe_path 缓存
        self._records_dirty = True
        self._stats_tick = 0
        self._minimized = False
        self._minimize_hint_shown = False
        self._tray_enabled = False
        self._tray_icon_path = None
        self._bg_proc = None
        # 前台窗口预览放后台线程，默认休眠；切到设置页时再激活，平时零开销
        self._preview = ForegroundPreviewWorker()
        self._preview.set_active(False)
        self._preview.start()

        self.root = tk.Tk()
        self.root.title("屏幕使用时间")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Map>", lambda _e: self._on_map())
        self.root.bind("<Configure>",
                       lambda e: self._on_root_resize() if e.widget is self.root else None)
        theme.init_font(self.root)
        self._dpi = self._setup_dpi()
        self.root.geometry(f"{int(1200 * self._dpi)}x{int(780 * self._dpi)}")
        self.root.minsize(int(760 * self._dpi), int(560 * self._dpi))
        self.root.configure(bg=theme.BG)
        set_icon_root(self.root)

        self._setup_styles()
        self._build_sidebar()
        self._build_pages()
        self._setup_tray()
        self._spawn_background()
        self.show_page("home")
        if start_minimized and self._tray_enabled:
            self._hide_to_tray(silent=True)
        else:
            self._check_overlap_sessions()
            # 延迟首次定时循环与统计预热，窗口先显示出来
            self._boot_refresh_timer = self.root.after(150, self._refresh_loop)
            self._boot_warm_timer = self.root.after(500, self._warm_stats)
        self._schedule_hourly_stats()


def run_app(db: UsageDB, cfg: dict, report_dir: Path, *, start_minimized: bool = False):
    # 单实例唤醒：若已有 GUI 常驻托盘，直接唤醒旧窗口并退出，避免多开窗口
    if not start_minimized and tray.wake_existing_instance():
        return
    app = ScreenTimeApp(db, cfg, report_dir, start_minimized=start_minimized)
    try:
        app.run()
    finally:
        app.shutdown()
