"""Windows 浅色风格屏幕使用时间 GUI（只读展示，采集由后台子进程自动启动）。"""
from __future__ import annotations

import ctypes
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from . import theme, tray
from .config import project_root
from .db import UsageDB
from .monitor import get_foreground_info  # 仅用于“当前前台窗口”预览，不参与计时
from .overrides import override_for, remove_override, set_override
from .ui import (
    BackgroundMixin, RecordsPageMixin, SettingsPageMixin, StatsPageMixin,
)
from .ui.common import shorten as _shorten, site_display as _site_display
from .utils import fmt_minsec
from .widgets import (
    FlowFrame, NavButton, ProgressBar, RoundedButton, RoundedCard, ScrollArea,
    get_brand_logo, get_default_icon, get_display_name, get_exe_icon, get_globe_icon,
    resolve_exe_path, rounded_polygon, set_icon_root,
)

NAV_PAGES = [
    ("home", "🏠", "首页"),
    ("stats", "📊", "统计"),
    ("records", "📋", "详细记录"),
    ("categories", "🗂️", "分类"),
]
MAX_ROWS = 5


def _enable_high_dpi():
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_high_dpi()


def rotate_log(path: Path, max_bytes: int = 1_000_000, keep: int = 3) -> None:
    """日志超过 ``max_bytes`` 时轮转：``x.log`` → ``x.log.1`` → ``x.log.2`` …

    长期运行的桌面程序如果只追加不轮转，日志会一直膨胀——用户机器上的
    ``data/ui_errors.log`` 实测已经涨到 500KB 以上。这里保留最近 ``keep`` 份。
    """
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    try:
        path.with_name(f"{path.name}.{keep}").unlink(missing_ok=True)
    except OSError:
        pass
    for index in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        try:
            if src.exists():
                src.replace(dst)
        except OSError:
            pass
    try:
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        pass


class ForegroundPreviewWorker:
    """后台采样「当前前台窗口」，供设置页预览使用。

    为什么需要它：``get_foreground_info()`` 内部会走到浏览器历史库查询——
    枚举 Chrome/Edge/Firefox 的配置目录、打开 History / places.sqlite，
    失败时还要把数据库整个复制到临时文件再查。即使已经有 3 秒查询节流和
    0.2 秒 SQLite 超时，遇到浏览器独占数据库或磁盘繁忙时，单次调用仍可能
    阻塞几百毫秒。这个调用原本发生在 Tk 主线程的刷新循环里（每 2 秒一次），
    于是设置页甚至整个界面会跟着顿一下。

    这里把采样整体挪到独立线程，主线程只读最近一次结果，不再碰 Win32
    与浏览器历史库。
    """

    def __init__(self, interval: float = 2.0):
        self._interval = max(0.5, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._ready = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="foreground-preview", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[bool, dict | None]:
        """返回 ``(是否已产出过结果, 最近一次前台窗口信息)``。

        主线程只调用这个只读方法，不做任何 Win32 / 数据库操作。
        """
        with self._lock:
            return self._ready, self._latest

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                info = get_foreground_info()
            except Exception:  # noqa: BLE001 - 预览失败绝不能影响界面
                info = None
            with self._lock:
                self._latest = info
                self._ready = True
            if self._stop.wait(self._interval):
                break


class ScreenTimeApp(StatsPageMixin, RecordsPageMixin, SettingsPageMixin, BackgroundMixin):
    def __init__(self, db: UsageDB, cfg: dict, report_dir: Path):
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
        self._last_refresh_date = None  # 跨 0 点检测：上次刷新时的日期
        self._card_total_labels = {}
        self._records_dirty = True
        self._stats_tick = 0
        self._minimized = False
        self._minimize_hint_shown = False
        self._tray_enabled = False
        self._tray_icon_path = None
        self._bg_proc = None
        # 前台窗口预览放后台线程：主线程刷新时不再直接读浏览器历史库
        self._preview = ForegroundPreviewWorker()
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
        self.show_page("home")
        self._setup_tray()
        self._spawn_background()
        self._check_overlap_sessions()
        self.root.after(150, self._refresh_loop)  # 延迟首次刷新，窗口先显示出来
        self.root.after(500, self._warm_stats)     # 启动后先渲染一次统计图
        self._schedule_hourly_stats()
        self._stats_redraw_timer = None

    # ---------- DPI ----------
    def _setup_dpi(self) -> float:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi <= 0:
                dpi = 96
        except Exception:
            dpi = 96
        factor = dpi / 96.0
        theme.set_scale(factor)
        try:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        return factor

    # ---------- 基础 ----------
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        padding = (8, 5)
        style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF",
                        foreground=theme.TEXT, font=theme.font(10),
                        arrowcolor=theme.ACCENT, bordercolor="#D9D9DE", padding=padding)
        # 修复：下拉框点开不选后文字变白不可见——显式固定 readonly 状态配色
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#FFFFFF")],
                  foreground=[("readonly", theme.TEXT)],
                  selectbackground=[("readonly", theme.ACCENT)],
                  selectforeground=[("readonly", "#FFFFFF")])
        self.root.option_add("*TCombobox*Listbox.background", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.foreground", theme.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", theme.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style.configure("TSpinbox", fieldbackground="#FFFFFF", foreground=theme.TEXT,
                        font=theme.font(10), arrowsize=12, bordercolor="#D9D9DE",
                        padding=padding)
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=theme.TEXT,
                        font=theme.font(10), bordercolor="#D9D9DE", padding=padding)
        style.configure("TCheckbutton", background=theme.BG, foreground=theme.TEXT,
                        font=theme.font(10), padding=(4, 5))

    def _build_sidebar(self):
        k = theme.scale(1.0)
        self.sidebar = tk.Frame(self.root, bg=theme.SIDEBAR_BG,
                                width=theme.scale(theme.SIDEBAR_WIDTH))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        brand.pack(fill="x", pady=(theme.scale(24), theme.scale(20)))
        self._make_brand(brand)

        nav_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        nav_frame.pack(fill="x")
        self.nav_buttons = {}
        for key, icon, text in NAV_PAGES:
            btn = NavButton(nav_frame, text=text, icon=icon,
                            command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=theme.scale(10), pady=theme.scale(3))
            self.nav_buttons[key] = btn

        settings_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        settings_frame.pack(side="bottom", fill="x")
        self.nav_buttons["settings"] = NavButton(
            settings_frame, text="设置", icon="⚙️",
            command=lambda: self.show_page("settings"))
        self.nav_buttons["settings"].pack(fill="x", padx=theme.scale(10), pady=theme.scale(3))

        status_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        status_frame.pack(side="bottom", fill="x", pady=(0, theme.scale(18)))
        self.status_dot = tk.Label(status_frame, text="●", font=("Segoe UI", 10),
                                   fg=theme.SUB, bg=theme.SIDEBAR_BG)
        self.status_dot.pack(side="left", padx=(theme.scale(14), 4))
        self.status_text = tk.Label(status_frame, text="后台未运行", font=theme.font(9),
                                    fg=theme.SUB, bg=theme.SIDEBAR_BG)
        self.status_text.pack(side="left", padx=(0, theme.scale(6)))

    def _make_brand(self, parent):
        canvas = tk.Canvas(parent, width=theme.scale(136), height=theme.scale(48),
                           bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0)
        canvas.bind("<Configure>", lambda _e: self._draw_brand(canvas))
        canvas.bind("<Map>", lambda _e: self._draw_brand(canvas))
        canvas.pack(anchor="w", padx=theme.scale(12))

    def _draw_brand(self, canvas):
        canvas.delete("all")
        k = theme.scale(1.0)
        x0, y0 = 0, 4 * k
        side = 38 * k
        logo = get_brand_logo(int(side))
        if logo is not None:
            self._brand_photo = logo  # 持有引用，防止被回收
            canvas.create_image(x0, y0, image=logo, anchor="nw")
        else:
            # 资源缺失时回退到旧版手绘图标
            base = rounded_polygon(x0, y0, x0 + side, y0 + side, 11 * k)
            canvas.create_polygon(base, smooth=True, fill=theme.ACCENT, outline="")
            gloss = rounded_polygon(x0 + 1, y0 + 1, x0 + side - 1, y0 + side * 0.42, 9 * k)
            canvas.create_polygon(gloss, smooth=True, fill="#3395FF", outline="")
            cx, cy, r = x0 + side / 2, y0 + side / 2, 13 * k
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               outline="#FFFFFF", width=2.2 * k)
            canvas.create_line(cx, cy, cx, cy - 6.5 * k, fill="#FFFFFF",
                               width=2.2 * k, capstyle="round")
            canvas.create_line(cx, cy, cx + 5 * k, cy + 4 * k, fill="#FFFFFF",
                               width=2.2 * k, capstyle="round")
        tx = x0 + side + theme.scale(10)
        canvas.create_text(tx, y0 + theme.scale(7), text="屏幕时间", anchor="w",
                           font=theme.font(15, True), fill=theme.TEXT_TITLE)
        canvas.create_text(tx, y0 + side - theme.scale(9), text="使用时长记录", anchor="w",
                           font=theme.font(9), fill=theme.SUB)

    def _build_pages(self):
        self.main = tk.Frame(self.root, bg=theme.BG)
        self.main.pack(side="left", fill="both", expand=True)
        self.pages = {key: tk.Frame(self.main, bg=theme.BG) for key, _, _ in NAV_PAGES}
        self.pages["settings"] = tk.Frame(self.main, bg=theme.BG)
        self._build_home()
        self._build_stats()
        self._build_records()
        self._build_categories()
        self._build_settings()

    def show_page(self, key: str):
        self.current_page = key
        for k, page in self.pages.items():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for nk, btn in self.nav_buttons.items():
            btn.set_selected(nk == key)
        if key == "records":
            self._records_dirty = True
            self._refresh_records()
        elif key == "stats":
            self._schedule_stats_draw()

    # ---------- 首页 ----------
    def _build_home(self):
        page = self.pages["home"]
        # 最小宽度调小：允许内容随窗口变窄重排（双卡片自动竖排）
        sc = ScrollArea(page, bg=theme.BG, min_width=380, respect_req=False)
        sc.pack(fill="both", expand=True)
        inner = sc.inner

        header = tk.Frame(inner, bg=theme.BG)
        header.pack(fill="x", padx=theme.scale(28), pady=(theme.scale(32), 0))
        tk.Label(header, text="概览", font=theme.font(22, True),
                 fg=theme.TEXT_TITLE, bg=theme.BG).pack(side="left")
        tabs = tk.Frame(header, bg=theme.BG)
        tabs.pack(side="right")
        self.tab_buttons = {}
        for t in ("今日", "本周"):
            btn = RoundedButton(tabs, text=t, width=72, height=30, radius=6,
                                selectable=True, selected=(t == self.period),
                                font=theme.font(10), fill=theme.ACCENT, fg="#FFFFFF",
                                bg=theme.BG, command=lambda t=t: self.set_period(t))
            btn.pack(side="left", padx=2)
            self.tab_buttons[t] = btn

        # 跨 0 点提示横幅（默认隐藏，日期变化时显示几秒后自动消失）
        self.day_banner = tk.Label(inner, text="", font=theme.font(9), fg=theme.ACCENT,
                                   bg=theme.ACCENT_LIGHTER, anchor="w", justify="left",
                                   padx=theme.scale(10), pady=4)
        self._banner_timer = None

        self.freq_title = tk.Label(inner, text="最为频繁", font=theme.font(12, True),
                                   fg=theme.SUB, bg=theme.BG)
        self.freq_title.pack(anchor="w", padx=theme.scale(30), pady=(theme.scale(16), 8))

        cards = tk.Frame(inner, bg=theme.BG)
        cards.pack(fill="x", padx=theme.scale(28))
        cards.bind("<Configure>", lambda _e: self._relayout_home_cards())
        cards.grid_columnconfigure(0, weight=1, uniform="hcard")
        cards.grid_columnconfigure(1, weight=1, uniform="hcard")
        self.app_card = self._make_freq_card(cards, "应用", "📦", theme.CARD_BG, 0)
        self.site_card = self._make_freq_card(cards, "网站", "🌐", theme.ACCENT_LIGHTER, 1)

        filter_row = tk.Frame(inner, bg=theme.BG)
        filter_row.pack(fill="x", padx=theme.scale(30), pady=(theme.scale(16), 8))
        tk.Label(filter_row, text="分类", font=theme.font(10), fg=theme.SUB,
                 bg=theme.BG).pack(side="left", padx=(0, 8))
        self.filter_combo = ttk.Combobox(filter_row, values=("全部", "应用", "游戏", "网站"),
                                         state="readonly", width=8)
        self.filter_combo.set(self.filter)
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_filter())
        self.filter_combo.pack(side="left")

        mini_row = FlowFrame(inner, bg=theme.BG, item_width=theme.scale(210),
                             item_height=theme.scale(92), gap=theme.scale(12))
        mini_row.pack(fill="x", padx=theme.scale(24), pady=(theme.scale(8), theme.scale(24)))
        self.mini_row = mini_row
        self.mini_cards = []
        for bg, fg in theme.MINI_CARDS:
            card = RoundedCard(mini_row, fill=bg, radius=theme.CARD_RADIUS,
                               width=theme.scale(240), height=theme.scale(92))
            mini_row.add(card)
            value = tk.Label(card, text="--", font=theme.font(17, True), fg=fg, bg=bg)
            caption = tk.Label(card, text="", font=theme.font(9), fg=fg, bg=bg)
            card.create_window(theme.scale(14), theme.scale(14), window=value, anchor="nw")
            card.create_window(theme.scale(14), theme.scale(52), window=caption, anchor="nw")
            self.mini_cards.append((card, value, caption))

    def _make_freq_card(self, parent, title, icon, fill, col):
        card = RoundedCard(parent, fill=fill, radius=theme.CARD_RADIUS, height=theme.scale(390))
        card.grid(row=0, column=col, sticky="nsew", padx=theme.scale(8), pady=theme.scale(4))
        head = tk.Frame(card, bg=fill)
        card.create_window(theme.scale(16), theme.scale(14), window=head, anchor="nw")
        tk.Label(head, text=icon, font=("Segoe UI Emoji", 13), bg=fill).pack(side="left")
        tk.Label(head, text=title, font=theme.font(12, True), fg=theme.TEXT,
                 bg=fill).pack(side="left", padx=(6, 0))
        total = tk.Label(head, text="", font=theme.font(10), fg=theme.SUB, bg=fill)
        total.pack(side="left", padx=12)
        self._card_total_labels[title] = total
        self._card_rows[title] = []
        return card

    def _relayout_home_cards(self):
        """首页双卡片：横向放得下就并排，放不下自动竖排。"""
        cards = self.app_card.master
        w = cards.winfo_width()
        if w <= 10:
            return
        for c in (self.app_card, self.site_card):
            c.grid_forget()
        card_w = (w - theme.scale(16)) / 2.0
        if card_w >= theme.scale(300):
            self.app_card.grid(row=0, column=0, sticky="nsew",
                               padx=theme.scale(8), pady=theme.scale(4))
            self.site_card.grid(row=0, column=1, sticky="nsew",
                                padx=theme.scale(8), pady=theme.scale(4))
            cards.grid_columnconfigure(0, weight=1, uniform="hcard")
            cards.grid_columnconfigure(1, weight=1, uniform="hcard")
        else:
            self.app_card.grid(row=0, column=0, sticky="ew", pady=theme.scale(6))
            self.site_card.grid(row=1, column=0, sticky="ew", pady=theme.scale(6))
            cards.grid_columnconfigure(0, weight=1)

    def set_period(self, period: str):
        self.period = period
        for t, btn in self.tab_buttons.items():
            btn.set_selected(t == period)
        self.refresh()

    def _apply_filter(self):
        self.filter = self.filter_combo.get() or self.filter or "应用"
        self.filter_combo.set(self.filter)
        self.refresh()

    def _period_start(self) -> datetime:
        now = datetime.now()
        if self.period == "本周":
            monday = now - timedelta(days=now.weekday())
            return monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _exe_for_process(self, process: str) -> str:
        exe = resolve_exe_path(process)
        if exe:
            return exe
        try:
            row = self.db.conn.execute(
                "SELECT exe_path FROM sessions WHERE process=? AND exe_path<>'' "
                "ORDER BY id DESC LIMIT 1", (process,)).fetchone()
            if row and row[0]:
                return row[0]
        except Exception as exc:  # noqa: BLE001
            self._log_error("exe_lookup", exc)
        return ""

    def _process_item(self, a, full_name=False):
        exe = self._exe_for_process(a["process"])
        name = get_display_name(a["process"], exe)
        icon = get_exe_icon(exe, 40) if exe else get_default_icon(40)
        return {"icon": icon,
                "name": name if full_name else _shorten(name, 18),
                "seconds": a["seconds"],
                "category": a["category"],
                "process": a["process"]}

    def _site_item(self, s, full_name=False):
        return {"icon": get_globe_icon(40),
                "name": (_site_display(s) if full_name else _shorten(_site_display(s), 18)),
                "seconds": s["seconds"], "category": "网站"}

    def _sync_card(self, card, card_key, items, total, fill, bar_color,
                   show_pct=False, full_name=False, actions=False):
        # 窗口未布局/最小化时 winfo_width 可能返回 1，用缓存宽度兜底，避免
        # 以 1px 宽度重建行导致布局异常；布局成功后更新缓存
        w = card.winfo_width()
        if w > 10:
            self._card_widths[card_key] = w
        else:
            w = self._card_widths.get(card_key, theme.scale(640))
        width = max(theme.scale(280), w - theme.scale(32))
        sig = (width, [it["name"] for it in items])
        if self._rows_sig.get(card_key) != sig:
            # 重建必须“原子化”：只有重建成功才更新 sig；若中途异常（比如
            # 图标/控件创建失败），清空行引用并抛出，由上层记录日志。
            # 这样下次刷新 sig 仍旧，会自动重试，不会出现“头部有数据、
            # 行列表永久消失”的状态。
            try:
                card.delete("rowwin")
                self._build_card_rows(card, card_key, items, fill, bar_color,
                                      width, show_pct, full_name, actions)
                self._rows_sig[card_key] = sig
            except Exception:
                self._card_rows[card_key] = []
                raise
            # 重建后立即填充时长/进度，不用等下一个刷新周期
            self._update_card_rows(card_key, items, total, show_pct, full_name)
        else:
            self._update_card_rows(card_key, items, total, show_pct, full_name)

    def _build_card_rows(self, card, card_key, items, fill, bar_color, width,
                         show_pct=False, full_name=False, actions=False):
        rows = []
        if not items:
            items = [{"icon": None, "name": "今日暂无记录", "seconds": 0, "category": ""}]
        y = theme.scale(54)
        # 名称可用宽度：预留图标列与右侧时长列后，超长名称自动换行显示完整
        name_area = max(theme.scale(120), width - theme.scale(150))
        for it in items:
            # 单行防御：某一行构建失败（如控件/图标异常）只跳过该行，
            # 不拖垮整张卡片，保证其他行正常显示
            try:
                row = tk.Frame(card, bg=fill)
                icon_lbl = tk.Label(row, image=it["icon"] if it["icon"] else get_default_icon(40),
                                    bg=fill)
                name_lbl = tk.Label(row, text=it["name"], font=theme.font(10, True),
                                    fg=theme.TEXT, bg=fill, anchor="w",
                                    justify="left", wraplength=name_area)
                bar = ProgressBar(row, width=max(theme.scale(60), width - theme.scale(220)),
                                  height=8, fill=bar_color)
                time_lbl = tk.Label(row, text="", font=theme.font(9), fg=theme.SUB,
                                    bg=fill, anchor="e")
                actions_frame = None
                extra_h = 0
                if actions and it.get("process") and it.get("category") in ("应用", "游戏"):
                    proc = it["process"]
                    target = "游戏" if it["category"] == "应用" else "应用"
                    actions_frame = tk.Frame(row, bg=fill)
                    RoundedButton(
                        actions_frame, text=f"移到{target}",
                        command=self._make_mover(proc, target), width=64, height=22,
                        radius=5, fill=theme.ACCENT_LIGHTER, fg=theme.ACCENT,
                        bg=fill, font=theme.font(8, True),
                    ).pack(side="left", padx=(0, theme.scale(6)))
                    if override_for(proc):
                        RoundedButton(
                            actions_frame, text="还原自动",
                            command=self._make_mover(proc, None), width=64, height=22,
                            radius=5, fill=theme.SECONDARY_BG, fg=theme.SUB,
                            bg=fill, font=theme.font(8, True),
                        ).pack(side="left")
                    extra_h = theme.scale(26)
                # 名称换行到多行时行高自动加高，保证名称、时间条、百分比完整显示
                # 余量需覆盖：名称上方留白 + 第二行时间条/时长标签高度
                row_h = max(theme.scale(58), name_lbl.winfo_reqheight() + theme.scale(36)) + extra_h
                row.configure(height=row_h)
                row.grid_propagate(False)
                # 两行布局：名称与时间条同列（左对齐），时间条列弹性伸缩
                icon_lbl.grid(row=0, column=0, rowspan=3 if actions_frame else 2,
                              padx=(theme.scale(16), theme.scale(10)), pady=theme.scale(6))
                name_lbl.grid(row=0, column=1, columnspan=2, sticky="w",
                              padx=(0, theme.scale(16)), pady=(theme.scale(8), 0))
                bar.grid(row=1, column=1, sticky="ew", pady=(0, theme.scale(6)))
                time_lbl.grid(row=1, column=2, sticky="e",
                              padx=(theme.scale(8), theme.scale(16)), pady=(0, theme.scale(6)))
                if actions_frame:
                    actions_frame.grid(row=2, column=1, columnspan=2, sticky="e",
                                       padx=(0, theme.scale(16)), pady=(2, theme.scale(4)))
                row.grid_columnconfigure(1, weight=1)  # 时间条列吸收窗口缩放
                card.create_window(theme.scale(16), y, window=row, anchor="nw",
                                   width=width, tags="rowwin")
                rows.append({"icon": icon_lbl, "name": name_lbl, "bar": bar,
                             "time": time_lbl, "actions": actions_frame})
                y += row_h + theme.scale(6)
            except Exception:
                continue
        self._card_rows[card_key] = rows
        # 记录卡片内容总高度（头部 + 各行实际高度 + 底部留白），供卡片高度自适应
        self._card_heights[card_key] = y + theme.scale(10)

    def _empty_guide(self, hint_week=False):
        """今日无记录时的引导行：附昨日总时长，避免跨 0 点后误以为数据丢失。"""
        try:
            now = datetime.now()
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            secs = self.db.total_seconds_between(today - timedelta(days=1), today)
            if secs > 0:
                extra = "（可切“本周”查看）" if hint_week else ""
                return [{"icon": None,
                         "name": f"今日暂无记录 · 昨日共 {fmt_minsec(secs)}{extra}",
                         "seconds": 0, "category": ""}]
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)
        return [{"icon": None, "name": "今日暂无记录", "seconds": 0, "category": ""}]

    def _update_card_rows(self, card_key, items, total, show_pct=False, full_name=False):
        rows = self._card_rows.get(card_key, [])
        if not items:
            items = [{"icon": None, "name": "今日暂无记录", "seconds": 0, "category": ""}]
        for i, row in enumerate(rows):
            if i >= len(items):
                break
            it = items[i]
            row["icon"].config(image=it["icon"] if it["icon"] else get_default_icon(40))
            row["name"].config(text=it["name"])
            ratio = it["seconds"] / total if total > 0 else 0.0
            row["bar"].set(ratio)
            if it["seconds"] > 0:
                text = fmt_minsec(it["seconds"])
                if show_pct and total > 0:
                    pct = it["seconds"] / total * 100
                    pct_text = f"{pct:.1f}%" if pct < 10 else f"{pct:.0f}%"
                    text += f" · {pct_text}"
                row["time"].config(text=text)
            else:
                row["time"].config(text="")

    def _period_snapshot(self, start, end):
        """同一时间窗内只查一次库，供首页/小卡片/分类页共用。"""
        return {
            "start": start,
            "end": end,
            "apps": self.db.desktop_summary_between(start, end),
            "sites": self.db.sites_summary_between(start, end),
            "cats": self.db.category_summary_between(start, end),
        }

    def _refresh_home(self, start, end, snapshot=None):
        snap = snapshot or self._period_snapshot(start, end)
        apps = snap["apps"]
        sites = snap["sites"]
        app_total = sum(a["seconds"] for a in apps)
        site_total = sum(s["seconds"] for s in sites)
        self._card_total_labels["应用"].config(text=f"共 {fmt_minsec(app_total)}")
        self._card_total_labels["网站"].config(text=f"共 {fmt_minsec(site_total)}")

        app_items = [self._process_item(a) for a in apps[:MAX_ROWS]] or self._empty_guide(hint_week=True)
        site_items = [self._site_item(s) for s in sites[:MAX_ROWS]] or self._empty_guide(hint_week=True)
        self._sync_card(self.app_card, "应用", app_items, app_total, theme.CARD_BG, theme.ACCENT)
        self._sync_card(self.site_card, "网站", site_items, site_total,
                        theme.ACCENT_LIGHTER, theme.ACCENT)
        self._update_mini_cards(start, end, snapshot=snap)

    def _update_mini_cards(self, start, end, snapshot=None):
        snap = snapshot or self._period_snapshot(start, end)
        cats = snap["cats"]
        apps = snap["apps"]
        sites = snap["sites"]
        total_all = sum(c["seconds"] for c in cats.values())
        total_app = cats.get("应用", {}).get("seconds", 0)
        total_game = cats.get("游戏", {}).get("seconds", 0)
        total_site = cats.get("网站", {}).get("seconds", 0)

        def count(cat):
            return cats.get(cat, {}).get("count", 0)

        top_app = apps[0]["process"] if apps else "--"
        top_game = next((a["process"] for a in apps if a["category"] == "游戏"), "--")
        top_site = _site_display(sites[0]) if sites else "--"
        prefix = "今日" if self.period == "今日" else "本周"

        if self.filter == "全部":
            values = [
                (fmt_minsec(total_all), f"{prefix}总时长"),
                (top_app, "最常用软件"),
                (top_site, "最常访问网站"),
                (fmt_minsec(total_game), "游戏时长"),
            ]
        elif self.filter == "网站":
            pct = f"{total_site / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_site), f"{prefix}网站时长"),
                (top_site, "最常访问网站"),
                (f"{count('网站')} 个", "网站数量"),
                (pct, "占当日比例"),
            ]
        elif self.filter == "游戏":
            pct = f"{total_game / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_game), f"{prefix}游戏时长"),
                (top_game, "最常玩游戏"),
                (f"{count('游戏')} 个", "游戏数量"),
                (pct, "占当日比例"),
            ]
        else:
            pct = f"{total_app / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_app), f"{prefix}应用时长"),
                (top_app, "最常用软件"),
                (f"{count('应用')} 个", "应用数量"),
                (pct, "占当日比例"),
            ]
        for (card, value_lbl, caption_lbl), (value, caption) in zip(self.mini_cards, values):
            value_lbl.config(text=str(value))  # 完整显示，不截断
            caption_lbl.config(text=caption)
            # 卡片宽度不低于内容宽度，保证文字不被裁掉
            try:
                need = max(value_lbl.winfo_reqwidth(), caption_lbl.winfo_reqwidth())
                card.configure(width=max(theme.scale(240), need + theme.scale(28)))
            except tk.TclError:
                pass
        try:
            self.mini_row._relayout()  # 按新宽度重新排布，避免列宽过窄裁掉内容
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)

    # ---------- 分类页（恒三栏横排，窄窗横向滚动） ----------
    def _build_categories(self):
        """分类页：完全复用首页的卡片+行渲染方式（同样的行控件与挂载逻辑）。"""
        page = self.pages["categories"]
        sc = ScrollArea(page, bg=theme.BG, min_width=380, respect_req=False)
        sc.pack(fill="both", expand=True)
        inner = sc.inner
        tk.Label(inner, text="分类", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(anchor="w", padx=theme.scale(28),
                                   pady=(theme.scale(32), theme.scale(16)))
        tk.Label(inner, text="提示：点击进程右侧按钮可手动调整“应用 / 游戏”，手动设置永久优先于自动识别",
                 font=theme.font(9), fg=theme.SUB, bg=theme.BG).pack(
            anchor="w", padx=theme.scale(28), pady=(0, theme.scale(12)))
        self.cat_grid = tk.Frame(inner, bg=theme.BG)
        self.cat_grid.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(24)))
        self.cat_grid.bind("<Configure>", lambda _e: self._relayout_categories())
        self.cat_cards = {}
        for cat in ("应用", "游戏", "网站"):
            icon, _color = theme.CATEGORY_META[cat]
            card = RoundedCard(self.cat_grid, fill="#FFFFFF", radius=theme.CARD_RADIUS,
                               height=theme.scale(430))
            # 头部：与首页卡片一致（图标 + 标题 + 总计/占比）
            head = tk.Frame(card, bg="#FFFFFF")
            card.create_window(theme.scale(16), theme.scale(14), window=head, anchor="nw")
            tk.Label(head, text=icon, font=("Segoe UI Emoji", 13), bg="#FFFFFF").pack(side="left")
            tk.Label(head, text=cat, font=theme.font(13, True), fg=theme.TEXT,
                     bg="#FFFFFF").pack(side="left", padx=(6, 0))
            total = tk.Label(head, text="共 --", font=theme.font(10), fg=theme.SUB,
                             bg="#FFFFFF")
            total.pack(side="left", padx=12)
            self.cat_cards[cat] = {"card": card, "total": total}
            key = f"分类-{cat}"
            self._card_rows[key] = []
            self._rows_sig[key] = None
            card.grid(row=0, column=len(self.cat_cards) - 1, sticky="nsew",
                      padx=theme.scale(8), pady=theme.scale(4))
        for i in range(3):
            self.cat_grid.grid_columnconfigure(i, weight=1, uniform="cat",
                                               minsize=theme.scale(360))

    def _relayout_categories(self):
        """分类三卡：横向放得下就三栏并排，放不下自动竖排。"""
        w = self.cat_grid.winfo_width()
        if w <= 10:
            return
        cards = list(self.cat_cards.values())
        for c in cards:
            c["card"].grid_forget()
        # 卡片内容需要至少 360 逻辑宽度，三栏阈值相应提高，保证不裁内容
        if w >= theme.scale(360) * 3 + theme.scale(16):
            for i, c in enumerate(cards):
                c["card"].grid(row=0, column=i, sticky="nsew",
                               padx=theme.scale(8), pady=theme.scale(4))
            for i in range(3):
                self.cat_grid.grid_columnconfigure(i, weight=1, uniform="cat",
                                                   minsize=theme.scale(360))
        else:
            # 竖排：先清掉横向模式遗留的列配置，只保留第一列并撑满页面宽度
            for i in range(1, 3):
                self.cat_grid.grid_columnconfigure(i, weight=0, uniform="", minsize=0)
            self.cat_grid.grid_columnconfigure(0, weight=1, uniform="", minsize=0)
            for i, c in enumerate(cards):
                c["card"].grid(row=i, column=0, sticky="ew", pady=theme.scale(8))
        # 卡片重排后立即按最新宽度重建行（与首页同一渲染管线），不等下一个刷新周期
        try:
            self.root.after_idle(self._resync_category_rows)
        except tk.TclError:
            pass

    def _resync_category_rows(self):
        """按最新卡片宽度重建分类页行内容。"""
        try:
            now = datetime.now()
            self._refresh_categories(self._period_start(), now)
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)

    def _refresh_categories(self, start, end, snapshot=None):
        snap = snapshot or self._period_snapshot(start, end)
        cats = snap["cats"]
        apps = snap["apps"]
        sites = snap["sites"]
        total_all = sum(c["seconds"] for c in cats.values()) or 1
        for cat, widgets in self.cat_cards.items():
            secs = cats.get(cat, {}).get("seconds", 0)
            cnt = cats.get(cat, {}).get("count", 0)
            pct = secs / total_all * 100
            widgets["total"].config(text=f"共 {fmt_minsec(secs)} · 占 {pct:.0f}%")
            key = f"分类-{cat}"
            if cat == "网站":
                items = ([self._site_item(s, full_name=True) for s in sites]
                         or self._empty_guide())  # 全部网站
                self._sync_card(widgets["card"], key, items, secs, "#FFFFFF",
                                theme.CATEGORY_META[cat][1], show_pct=True, full_name=True)
            else:
                items = ([self._process_item(a, full_name=True) for a in apps
                          if a["category"] == cat]
                         or self._empty_guide())  # 全部进程
                # 与首页完全相同的行渲染管线，保证显示效果一致；进程行附带移动按钮
                self._sync_card(widgets["card"], key, items, secs, "#FFFFFF",
                                theme.CATEGORY_META[cat][1], show_pct=True, full_name=True,
                                actions=True)
            # 卡片高度按各行实际高度累加（名称换行时行高自动加高），保证全部进程/网站完整显示
            h = self._card_heights.get(key)
            if h:
                widgets["card"].configure(height=int(h))

    def _make_mover(self, process: str, target: str | None):
        """生成分类移动按钮回调（target=None 表示还原为自动识别）。"""
        return lambda: self._move_process(process, target)

    def _move_process(self, process: str, target: str | None):
        """手动把进程归到 应用/游戏，并同步重分类历史记录。"""
        try:
            if target in ("应用", "游戏"):
                set_override(process, target)
            else:
                remove_override(process)
                self.db.reclassify_process(process)
            self.db.apply_overrides()
        except Exception as exc:  # noqa: BLE001
            self._log_error("move_process", exc)
            return
        # 覆盖状态变化会影响按钮显示，强制重建分类页三卡
        for key in ("分类-应用", "分类-游戏", "分类-网站"):
            self._rows_sig.pop(key, None)
        try:
            self._refresh_categories(self._period_start(), datetime.now())
        except Exception as exc:  # noqa: BLE001
            self._log_error("move_refresh", exc)

    # ---------- 刷新循环 ----------
    def _refresh_loop(self):
        if not self.root.winfo_exists():
            return
        # 防御：refresh 内部任何未预期异常都不能中断 after 链，
        # 否则界面会永久冻结（只显示最后一次成功刷新的内容）
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self._log_error("refresh_loop", exc)
        self.root.after(2000, self._refresh_loop)

    def _warm_stats(self):
        """启动后预渲染统计图，打开统计页时立即可见。"""
        try:
            if self._stats_figs is None:
                self._draw_stats()
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)

    def _schedule_hourly_stats(self):
        """整点刷新统计图（补上上一小时的数据）。"""
        try:
            now = datetime.now()
            nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            delay_ms = int((nxt - now).total_seconds() * 1000) + 500
            self._hourly_timer = self.root.after(delay_ms, self._on_hourly_stats)
        except tk.TclError:
            pass

    def _on_hourly_stats(self):
        try:
            if self._stats_figs is not None:
                self._draw_stats()
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)
        self._schedule_hourly_stats()

    def _on_root_resize(self):
        try:
            self.root.after_idle(self.refresh)
        except tk.TclError:
            pass
        # 全局窗口缩放时，强制重置统计图尺寸缓存、启动防抖重绘，
        # 否则画布沿用旧尺寸，切回统计页时只显示左上角一部分
        if getattr(self, "_stats_figs", None) is None:
            return
        new_size = (self.root.winfo_width(), self.root.winfo_height())
        if getattr(self, "_stats_root_size", None) == new_size:
            return
        self._stats_root_size = new_size
        self._stats_fixed_size = None
        if getattr(self, "_stats_redraw_timer", None):
            try:
                self.root.after_cancel(self._stats_redraw_timer)
            except tk.TclError:
                pass
        if self.current_page == "stats":
            self._stats_redraw_timer = self.root.after(60, self._draw_stats)

    def _show_day_banner(self):
        """跨 0 点后显示提示横幅：新的一天已开始，数据已切换到今日。"""
        try:
            b = self.day_banner
            b.config(text="🌅 新的一天已开始 · 页面已切换到今日数据（昨日数据可切“本周”查看）")
            b.pack(fill="x", padx=theme.scale(28), pady=(theme.scale(8), 0),
                   before=self.freq_title)
            if self._banner_timer:
                self.root.after_cancel(self._banner_timer)
            self._banner_timer = self.root.after(8000, self._hide_day_banner)
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)

    def _hide_day_banner(self):
        try:
            self.day_banner.pack_forget()
        except Exception as exc:  # noqa: BLE001
            # 界面容错不能变成「静默吞掉」：统一落日志，traceback 里有精确位置
            self._log_error("ui", exc)

    def refresh(self):
        # 下拉框点开不选被清空时，自动恢复当前值
        try:
            if not self.filter_combo.get():
                self.filter_combo.set(self.filter)
        except Exception as exc:  # noqa: BLE001
            self._log_error("combo", exc)

        now = datetime.now()
        start = self._period_start()

        # 跨 0 点检测：日期变化后强制重建所有卡片，避免旧一天的行/签名残留
        today = now.date()
        if self._last_refresh_date is not None and self._last_refresh_date != today:
            self._rows_sig.clear()
            self._card_widths.clear()
            self._records_sig = None
            self._records_dirty = True
            self._stats_fixed_size = None
            self._show_day_banner()
        self._last_refresh_date = today

        # 各模块独立刷新，互不拖累（首页出错不影响分类 TOP3）；
        # 同一时间窗只查一次库，snapshot 复用 apps/sites/cats
        try:
            snap = self._period_snapshot(start, now)
        except Exception as exc:  # noqa: BLE001
            self._log_error("snapshot", exc)
            snap = None
        try:
            self._refresh_home(start, now, snapshot=snap)
        except Exception as exc:  # noqa: BLE001
            self._log_error("home", exc)
        try:
            self._refresh_categories(start, now, snapshot=snap)
        except Exception as exc:  # noqa: BLE001
            self._log_error("categories", exc)
        try:
            self._refresh_settings_live(now)
        except Exception as exc:  # noqa: BLE001
            self._log_error("settings_live", exc)
        try:
            running = self._background_running()
            if running:
                self.status_dot.config(fg=theme.ACCENT)
                self.status_text.config(text="后台采集运行中")
            else:
                self.status_dot.config(fg=theme.SUB)
                self.status_text.config(text="后台未运行")
        except Exception as exc:  # noqa: BLE001
            self._log_error("status", exc)
        if self.current_page == "records" and self._records_dirty:
            try:
                self._records_dirty = False
                self._refresh_records()
            except Exception as exc:  # noqa: BLE001
                self._log_error("records", exc)

    def _log_error(self, where: str, exc: Exception):
        """把界面异常写入 data/ui_errors.log，便于排查 pythonw 下的静默错误。

        文件超过 1MB 时自动轮转（保留最近 3 份），避免长期运行无限增长。
        """
        try:
            import traceback as _tb
            log_dir = project_root() / self.cfg.get("data_dir", "data")
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "ui_errors.log"
            rotate_log(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}: {exc}\n")
                f.write(_tb.format_exc() + "\n")
        except Exception:  # noqa: BLE001
            # 日志本身失败时静默，绝不能反过来影响界面
            pass

    # ---------- 生命周期 ----------
    def _on_close(self):
        """点右上角 × ：隐藏到系统托盘（后台采集不受影响）。"""
        if self._tray_enabled:
            self._hide_to_tray()
        else:
            self.shutdown()

    def _hide_to_tray(self):
        self.root.withdraw()
        self.root.title("屏幕使用时间 · 后台记录中")
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            self.root.after(600, lambda: messagebox.showinfo(
                "后台记录中",
                "窗口已隐藏到系统托盘（任务栏右下角），后台采集不受影响。\n"
                "双击托盘图标恢复窗口；右键托盘图标可退出。"))

    def _on_map(self):
        try:
            if self.root.state() == "normal" and self._minimized:
                self._minimized = False
                self.root.title("屏幕使用时间")
        except tk.TclError:
            pass

    def _quit_app(self):
        self.shutdown()

    def shutdown(self):
        self._preview.stop()
        self._stop_background_process()
        if self._tray_enabled:
            try:
                tray.disable_tray()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_app(db: UsageDB, cfg: dict, report_dir: Path):
    app = ScreenTimeApp(db, cfg, report_dir)
    try:
        app.run()
    finally:
        app.shutdown()
