"""macOS 浅色风格屏幕使用时间 GUI（只读展示，采集由后台子进程自动启动）。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from . import theme, tray
from .config import project_root
from .db import UsageDB
from .monitor import get_foreground_info  # 仅用于“当前前台窗口”预览，不参与计时
from .overrides import override_for, remove_override, set_override
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
ROW_HEIGHT = 36


def _enable_high_dpi():
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_high_dpi()


def _shorten(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _site_display(s) -> str:
    site = (s.get("site") or "").strip()
    if "." in site and " " not in site and "/" not in site:
        return site
    return s.get("title") or site


_ROUNDTOP_REGISTERED = False


def _ensure_roundtop_style():
    """注册自定义 boxstyle：矩形 + 仅顶部两角圆角（二次贝塞尔，按像素圆滑）。"""
    global _ROUNDTOP_REGISTERED
    if _ROUNDTOP_REGISTERED:
        return
    from matplotlib.patches import BoxStyle
    from matplotlib.path import Path

    class _RoundTop:
        def __init__(self, pad=0.0, rounding_size=10.0):
            self.pad = pad
            self.rounding_size = rounding_size

        def __call__(self, x0, y0, width, height, mutation_size):
            pad = mutation_size * self.pad
            dr = mutation_size * self.rounding_size
            width, height = width + 2 * pad, height + 2 * pad
            x0, y0 = x0 - pad, y0 - pad
            x1, y1 = x0 + width, y0 + height
            dr = max(0.0, min(dr, width / 2.0, height))
            cp = [
                (x0, y0), (x1, y0),
                (x1, y1 - dr), (x1, y1), (x1 - dr, y1),
                (x0 + dr, y1), (x0, y1), (x0, y1 - dr),
                (x0, y0),
            ]
            com = [
                Path.MOVETO, Path.LINETO,
                Path.LINETO, Path.CURVE3, Path.CURVE3,
                Path.LINETO, Path.CURVE3, Path.CURVE3,
                Path.CLOSEPOLY,
            ]
            return Path(cp, com)

    BoxStyle._style_list["roundtop"] = _RoundTop

    class _RoundBottom:
        """矩形 + 仅底部两角圆角。"""

        def __init__(self, pad=0.0, rounding_size=5.0):
            self.pad = pad
            self.rounding_size = rounding_size

        def __call__(self, x0, y0, width, height, mutation_size):
            pad = mutation_size * self.pad
            dr = mutation_size * self.rounding_size
            width, height = width + 2 * pad, height + 2 * pad
            x0, y0 = x0 - pad, y0 - pad
            x1, y1 = x0 + width, y0 + height
            dr = max(0.0, min(dr, width / 2.0, height))
            cp = [
                (x0, y0 + dr), (x0, y0), (x0 + dr, y0),
                (x1 - dr, y0), (x1, y0), (x1, y0 + dr),
                (x1, y1), (x0, y1), (x0, y0 + dr),
            ]
            com = [
                Path.MOVETO, Path.CURVE3, Path.CURVE3,
                Path.LINETO, Path.CURVE3, Path.CURVE3,
                Path.LINETO, Path.LINETO, Path.CLOSEPOLY,
            ]
            return Path(cp, com)

    BoxStyle._style_list["roundbottom"] = _RoundBottom
    _ROUNDTOP_REGISTERED = True


class ScreenTimeApp:
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
        except Exception:
            pass
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
        except Exception:  # noqa: BLE001
            pass
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

    def _refresh_home(self, start, end):
        apps = self.db.desktop_summary_between(start, end)
        sites = self.db.sites_summary_between(start, end)
        app_total = sum(a["seconds"] for a in apps)
        site_total = sum(s["seconds"] for s in sites)
        self._card_total_labels["应用"].config(text=f"共 {fmt_minsec(app_total)}")
        self._card_total_labels["网站"].config(text=f"共 {fmt_minsec(site_total)}")

        app_items = [self._process_item(a) for a in apps[:MAX_ROWS]] or self._empty_guide(hint_week=True)
        site_items = [self._site_item(s) for s in sites[:MAX_ROWS]] or self._empty_guide(hint_week=True)
        self._sync_card(self.app_card, "应用", app_items, app_total, theme.CARD_BG, theme.ACCENT)
        self._sync_card(self.site_card, "网站", site_items, site_total,
                        theme.ACCENT_LIGHTER, theme.ACCENT)
        self._update_mini_cards(start, end)

    def _update_mini_cards(self, start, end):
        cats = self.db.category_summary_between(start, end)
        apps = self.db.desktop_summary_between(start, end)
        sites = self.db.sites_summary_between(start, end)
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
        except Exception:  # noqa: BLE001
            pass

    # ---------- 统计页 ----------
    def _build_stats(self):
        page = self.pages["stats"]
        # 移除ScrollArea！！统计页直接用普通Frame
        for w in page.winfo_children():
            w.destroy()
        page.configure(bg=theme.BG)

        tk.Label(page, text="统计", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(anchor="w", padx=theme.scale(28), pady=(theme.scale(32), theme.scale(16)))
        self.stats_container = tk.Frame(page, bg=theme.BG)
        self.stats_container.pack(fill="both", expand=True, padx=theme.scale(24), pady=(0, theme.scale(24)))

        self._stats_figs = None
        self._stats_canvases = []
        self._stats_frames = []
        self._stats_fixed_size = None
        self._stats_drawing = False
        self._stats_redraw_timer = None
        self.stats_container.bind("<Configure>", self._stats_on_configure)

    def _stats_on_configure(self, event):
        if self._stats_drawing or self.current_page != "stats":
            return
        container_w = event.width if event.width > 50 else None
        if container_w is None:
            return
        # 只要容器宽度发生改变，清空固定尺寸缓存，触发重绘
        old_w = self._stats_fixed_size[0] if self._stats_fixed_size else None
        if old_w != container_w:
            self._stats_fixed_size = None
            if hasattr(self, '_stats_redraw_timer') and self._stats_redraw_timer:
                self.root.after_cancel(self._stats_redraw_timer)
            self._stats_redraw_timer = self.root.after(50, self._draw_stats)

    def _schedule_stats_draw(self):
        if self._stats_drawing:
            return
        if self.current_page != "stats":
            return
        self._stats_fixed_size = None
        self._draw_stats(force=True)

    def _ensure_stats_canvases(self):
        if self._stats_figs is None:
            # 延迟加载 matplotlib：只有打开统计页才付出这个成本，加快启动
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            from .report import setup_cjk_font
            setup_cjk_font()
            self._stats_figs = [Figure(facecolor="#FFFFFF"), Figure(facecolor="#FFFFFF")]
            self._stats_canvases = []
            self._stats_frames = []
            for fig in self._stats_figs:
                frame = tk.Frame(self.stats_container, bg=theme.BG)
                frame.pack(fill="both", pady=(0, theme.scale(16)))
                canvas = FigureCanvasTkAgg(fig, master=frame)
                tk_widget = canvas.get_tk_widget()
                tk_widget.pack(anchor="nw")
                # matplotlib 默认在画布首次 <Map> 时按 DPI 把控件尺寸改成物理像素
                # （高 DPI 下约 1.5 倍），会覆盖 _draw_stats_impl 强制设置的固定尺寸，
                # 导致图表被撑大、只显示左上角一部分。这里接管 <Map>，尺寸始终由我们控制。
                tk_widget.bind("<Map>", self._on_stats_canvas_map)
                self._stats_canvases.append(canvas)
                self._stats_frames.append(frame)
        return self._stats_figs, self._stats_canvases

    def _on_stats_canvas_map(self, _event=None):
        """画布重新显示时（切页/重排），重新套用最近一次绘制的固定尺寸。"""
        if not self._stats_fixed_size:
            return
        fig_w, heights = self._stats_fixed_size
        factor = theme.scale(1.0)
        for i, canvas in enumerate(self._stats_canvases):
            fig_h = max(int(120 * factor), heights[i])
            try:
                canvas.get_tk_widget().config(width=fig_w, height=fig_h)
            except tk.TclError:
                pass

    def _draw_stats(self, force=False):
        if self._stats_drawing:
            return
        self._stats_drawing = True
        try:
            self._draw_stats_impl()
        finally:
            self._stats_drawing = False

    def _draw_stats_impl(self):
        figs, canvases = self._ensure_stats_canvases()
        for _ in range(3):
            try:
                self.stats_container.update_idletasks()
                self.root.update_idletasks()
            except tk.TclError:
                pass
        factor = theme.scale(1.0)
        container_w = self.stats_container.winfo_width()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        if container_w <= 50:
            container_w = int(root_w - theme.scale(theme.SIDEBAR_WIDTH) - theme.scale(48))
        if root_w <= 100 or root_h <= 100:
            return
        # 下限取 480*scale：最小窗口（760*scale）下容器仍有约 552*scale 宽，
        # 保证 fig_w 永远不会超过容器，避免画布被 pack 压缩、图表只显示左上角
        fig_w = int(max(theme.scale(480), container_w - theme.scale(8)))
        avail_h = int(root_h - theme.scale(96))
        total_h = max(int(200 * factor), avail_h - 110)
        heights = (int(total_h * 0.56), int(total_h * 0.42))
        self._stats_fixed_size = (fig_w, heights)
        for i, (fig, canvas) in enumerate(zip(figs, canvases)):
            fig_h = max(int(120 * factor), heights[i])
            fig.set_size_inches(fig_w / 100.0, fig_h / 100.0)
            tk_widget = canvas.get_tk_widget()
            # ==========关键改动==========
            # 每次强制赋值控件宽高，画布就可以支持缩小
            tk_widget.config(width=fig_w, height=fig_h)
            tk_widget.update_idletasks()
        self._draw_hourly(figs[0])
        self._draw_week(figs[1])
        for canvas in canvases:
            canvas.draw_idle()
            canvas.get_tk_widget().update_idletasks()

    def _hourly_usage(self, start, end):
        rows = self.db.conn.execute(
            "SELECT category, start_time, end_time FROM sessions "
            "WHERE start_time >= ? AND start_time < ?",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        ).fetchall()
        buckets = [dict() for _ in range(24)]
        for cat, s, e in rows:
            cur = datetime.fromisoformat(s)
            stop = datetime.fromisoformat(e)
            while cur < stop:
                hour = cur.hour
                nxt = (cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                seg_end = min(stop, nxt)
                buckets[hour][cat] = buckets[hour].get(cat, 0.0) + (seg_end - cur).total_seconds()
                cur = seg_end
        return buckets

    def _draw_hourly(self, fig):
        fig.clear()
        ax = fig.add_subplot(111)
        # 显式边距：保证左侧 Y 轴名完整显示
        # 缩小边距，让绘图区铺满白色底板（仍留出 Y 轴名与标题空间）
        fig.subplots_adjust(left=0.14, right=0.97, top=0.90, bottom=0.15)
        now = datetime.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets = self._hourly_usage(day_start, now)
        hours = list(range(24))
        bottom = [0.0] * 24
        segments = []
        for cat in ("应用", "网站", "游戏", "系统"):
            vals = [buckets[h].get(cat, 0.0) / 60.0 for h in hours]
            for h, v in enumerate(vals):
                if v > 0.01:
                    segments.append({"h": h, "y0": bottom[h], "v": v,
                                     "color": theme.CATEGORY_META[cat][1],
                                     "top": False, "bottom": False})
                    bottom[h] += v
        first = {}
        last = {}
        for i, seg in enumerate(segments):
            first.setdefault(seg["h"], i)
            last[seg["h"]] = i
        for i, seg in enumerate(segments):
            seg["top"] = (last[seg["h"]] == i)
            seg["bottom"] = (first[seg["h"]] == i)
        for seg in segments:
            self._add_bar_segment(ax, seg["h"], seg["y0"], 0.72, seg["v"],
                                  seg["color"], top=seg["top"], bottom=seg["bottom"])
        ax.set_xticks(list(range(0, 24, 3)))
        ax.set_xticklabels([f"{h}时" for h in range(0, 24, 3)], fontsize=9, color=theme.SUB)
        ax.set_xlim(-0.6, 23.6)
        ax.set_ylabel("分钟", fontsize=10, color=theme.SUB)
        ax.set_title(f"今日每小时使用 · 共 {sum(bottom):.0f} 分钟", fontsize=11, color=theme.TEXT_TITLE)
        ax.tick_params(colors=theme.SUB)
        from matplotlib.patches import Patch
        handles = [Patch(facecolor=theme.CATEGORY_META[c][1], label=c)
                   for c in ("应用", "网站", "游戏", "系统")]
        ax.legend(handles=handles, ncol=4, fontsize=9, frameon=False)
        ax.grid(axis="y", linestyle=":", color="#C8C8CC", alpha=0.15)
        ax.set_axisbelow(True)
        # 柱顶标注各小时的分钟数
        max_total = max(bottom) if bottom else 0
        for h, total in enumerate(bottom):
            if total > 0.5:
                ax.text(h, total + max_total * 0.02, f"{total:.0f}",
                        ha="center", va="bottom", fontsize=8, color=theme.SUB)
        ax.set_ylim(0, max_total * 1.16 + 2)
        # 平均每小时平均线：只统计有使用的时段，零时长不计入分母
        active_hours = sum(1 for v in bottom if v > 0.01)
        avg = sum(bottom) / active_hours if active_hours else 0.0
        if max_total > 0:
            ax.axhline(avg, color="#FF9F0A", linestyle="--", linewidth=1.2, alpha=0.9)
            ax.text(23.4, avg, f"活跃时段平均 {avg:.1f} 分", ha="right", va="bottom",
                    fontsize=9, color="#FF9F0A")

    def _draw_week(self, fig):
        fig.clear()
        ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.14, right=0.97, top=0.86, bottom=0.18)
        now = datetime.now()
        # 近 7 天 = 过去 7 天（今天往前推 6 天），不是本周一起点到未来
        week_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)
        days = 7
        daily = self.db.daily_category_totals(days)
        dates = [(week_start + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
        bottom = [0.0] * days
        has_data = False
        segments = []
        for cat in ("应用", "网站", "游戏", "系统"):
            vals = []
            for i in range(days):
                key = (week_start + timedelta(days=i)).strftime("%Y-%m-%d")
                vals.append(daily.get(key, {}).get(cat, 0.0) / 3600)
            if any(v > 0 for v in vals):
                has_data = True
            for i, v in enumerate(vals):
                if v > 0.01:
                    segments.append({"h": i, "y0": bottom[i], "v": v,
                                     "color": theme.CATEGORY_META[cat][1],
                                     "top": False, "bottom": False})
                    bottom[i] += v
        first = {}
        last = {}
        for i, seg in enumerate(segments):
            first.setdefault(seg["h"], i)
            last[seg["h"]] = i
        for i, seg in enumerate(segments):
            seg["top"] = (last[seg["h"]] == i)
            seg["bottom"] = (first[seg["h"]] == i)
        for seg in segments:
            self._add_bar_segment(ax, seg["h"], seg["y0"], 0.58, seg["v"],
                                  seg["color"], top=seg["top"], bottom=seg["bottom"])
        if not has_data:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center",
                    transform=ax.transAxes, color=theme.SUB)
        ax.set_ylabel("小时", fontsize=10, color=theme.SUB)
        ax.set_title("近 7 天使用趋势（按分类）", fontsize=11, color=theme.TEXT_TITLE)
        # 手动绘制圆角柱后横坐标不再自动生成，这里显式恢复日期刻度
        ax.set_xticks(list(range(days)))
        ax.set_xticklabels(dates, fontsize=9, color=theme.SUB)
        ax.set_xlim(-0.5, days - 0.5)
        ax.tick_params(colors=theme.SUB)
        from matplotlib.patches import Patch
        handles = [Patch(facecolor=theme.CATEGORY_META[c][1], label=c)
                   for c in ("应用", "网站", "游戏", "系统")]
        ax.legend(handles=handles, ncol=4, fontsize=9, frameon=False)
        ax.grid(axis="y", linestyle=":", color="#C8C8CC", alpha=0.15)
        ax.set_axisbelow(True)
        # 柱顶标注每天总小时数
        max_total = max(bottom) if bottom else 0
        for i, total in enumerate(bottom):
            if total > 0.05:
                ax.text(i, total + max_total * 0.02, f"{total:.1f}",
                        ha="center", va="bottom", fontsize=9, color=theme.SUB)
        if max_total > 0:
            ax.set_ylim(0, max_total * 1.16 + 0.3)
        # 平均每天平均线：只统计有使用的天数，零时长不计入分母
        active_days = sum(1 for v in bottom if v > 0.01)
        avg = sum(bottom) / active_days if active_days else 0.0
        if max_total > 0:
            ax.axhline(avg, color="#FF9F0A", linestyle="--", linewidth=1.2, alpha=0.9)
            ax.text(6.4, avg, f"活跃天数平均 {avg:.1f} 小时", ha="right", va="bottom",
                    fontsize=9, color="#FF9F0A")

    def _add_bar_segment(self, ax, x, y0, width, height, color,
                         top=False, bottom=False, radius_px=5):
        """柱段：整柱外轮廓做小的四分之一圆倒角；内部交界保持直角。"""
        if height <= 0.01:
            return
        from matplotlib.patches import FancyBboxPatch
        _ensure_roundtop_style()
        try:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            # 用图幅尺寸 + 子图边距推算像素密度，不依赖画布是否已渲染
            fig = ax.figure
            fig_w, fig_h = fig.get_size_inches()
            dpi = fig.dpi
            sp = fig.subplotpars
            axes_w = fig_w * dpi * (sp.right - sp.left)
            axes_h = fig_h * dpi * (sp.top - sp.bottom)
            px_x = axes_w / max(1e-6, xlim[1] - xlim[0])
            px_y = axes_h / max(1e-6, ylim[1] - ylim[0])
            mutation_aspect = px_x / px_y
            r_data = radius_px / max(1e-6, px_x)  # 像素 -> 数据单位
        except Exception:  # noqa: BLE001
            mutation_aspect = 1.0
            r_data = radius_px
        if top and bottom:
            # 单段柱：四个角都做小倒角（四分之一圆）
            boxstyle = f"round,pad=0,rounding_size={r_data}"
        elif top:
            boxstyle = f"roundtop,pad=0,rounding_size={r_data}"
        elif bottom:
            boxstyle = f"roundbottom,pad=0,rounding_size={r_data}"
        else:
            boxstyle = "square,pad=0"
        box = FancyBboxPatch(
            (x - width / 2.0, y0), width, height,
            boxstyle=boxstyle,
            mutation_aspect=mutation_aspect,
            facecolor=color, edgecolor="none",
        )
        ax.add_patch(box)

    # ---------- 详细记录页 ----------
    def _build_records(self):
        page = self.pages["records"]
        sc = ScrollArea(page, bg=theme.BG, min_width=680)
        sc.pack(fill="both", expand=True)
        inner = sc.inner
        tk.Label(inner, text="详细记录", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(anchor="w", padx=theme.scale(28), pady=(theme.scale(32), theme.scale(16)))
        self.records_inner = inner
        self._records_sig = None
        self._records_built = False

    def _refresh_records(self):
        end = datetime.now()
        start = self._period_start()
        inner = self.records_inner
        # 数据没变化时直接复用已构建的行，避免每次点开都全量销毁重建（卡顿根源）
        try:
            sig = self.db.conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(SUM(duration), 0) "
                "FROM sessions WHERE start_time >= ? AND start_time < ?",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchone()
        except Exception:  # noqa: BLE001
            sig = None
        if self._records_built and sig == self._records_sig:
            return
        self._records_sig = sig
        self._records_built = True
        # 取消可能还在进行中的分批构建
        self._records_build_id = getattr(self, "_records_build_id", 0) + 1
        for w in inner.winfo_children():
            if isinstance(w, tk.Label) and w.cget("text") == "详细记录":
                continue
            w.destroy()
        rows = self.db.recent_sessions_between(start, end, limit=2000)
        if not rows:
            tk.Label(inner, text="暂无记录", font=theme.font(11), fg=theme.SUB,
                     bg=theme.BG).pack(pady=40)
            return
        # 按「小时 + 相同进程」合并为一条记录（网站按站点合并），
        # 同一小时内反复出现同一程序只显示一行，时长累加。
        groups = {}
        for r in rows:
            try:
                dt = datetime.fromisoformat(r["start"])
            except Exception:
                dt = end
            hour = dt.replace(minute=0, second=0, microsecond=0)
            identity = (r["site"] or r["process"]) if r["category"] == "网站" else r["process"]
            key = (identity, hour, r["category"])
            g = groups.get(key)
            if g is None:
                g = {"identity": identity, "process": r["process"],
                     "site": r["site"] or "", "title": r["title"] or "",
                     "category": r["category"], "start": r["start"], "end": r["end"],
                     "seconds": 0.0, "count": 0, "hour": hour, "best": 0.0}
                groups[key] = g
            dur = float(r["duration"] or 0)
            g["seconds"] += dur
            g["count"] += 1
            if dur >= g["best"]:
                g["best"] = dur
                g["title"] = r["title"] or ""
                g["site"] = r["site"] or ""
        ordered = sorted(groups.values(),
                         key=lambda g: (g["start"], -g["seconds"]), reverse=True)
        # 分批流式构建：先画一批立即可见，其余用 idle 回调接力，避免一次创建几百行卡死界面
        self._records_pending = ordered
        self._records_chunk_i = 0
        self._build_records_chunk(self._records_build_id)

    def _build_record_row(self, inner, g):
        """创建单条记录行。"""
        row = tk.Frame(inner, bg="#FFFFFF", height=theme.scale(ROW_HEIGHT))
        row.pack_propagate(False)
        row.pack(fill="x", pady=2)
        if g["category"] == "网站":
            icon = get_globe_icon()
            name = _site_display(g)
            sub = ""
        else:
            exe = self._exe_for_process(g["process"])
            icon = get_exe_icon(exe) if exe else get_default_icon()
            name = get_display_name(g["process"], exe)
            sub = g["title"] if g["title"] else ""
        display = _shorten(name, 26) + (f"（{_shorten(sub, 20)}）" if sub else "")
        if g["count"] > 1:
            display += f" ×{g['count']}"
        t0 = g["hour"].strftime("%H:%M")
        t1 = (g["hour"] + timedelta(hours=1)).strftime("%H:%M")
        _, color = theme.CATEGORY_META.get(g["category"], ("📦", "#0A84FF"))
        time_lbl = tk.Label(row, text=fmt_minsec(g["seconds"]), font=theme.font(10),
                            fg=theme.TEXT, bg="#FFFFFF", width=14, anchor="e")
        time_lbl.pack(side="right", padx=theme.scale(16))
        range_lbl = tk.Label(row, text=f"{t0}-{t1}", font=theme.font(10), fg=theme.SUB,
                             bg="#FFFFFF", width=15, anchor="w")
        range_lbl.pack(side="left", padx=(theme.scale(16), 4))
        icon_lbl = tk.Label(row, image=icon, bg="#FFFFFF")
        icon_lbl.pack(side="left", padx=4)
        name_lbl = tk.Label(row, text=display, font=theme.font(10), fg=theme.TEXT,
                            bg="#FFFFFF", anchor="w")
        name_lbl.pack(side="left", padx=4, fill="x", expand=True)
        chip = tk.Label(row, text=g["category"], font=theme.font(9), fg="#FFFFFF",
                        bg=color, padx=6)
        chip.pack(side="left", padx=8)
        # 分类小标签保持原色，hover 只改变行背景与文字颜色
        targets = (time_lbl, range_lbl, icon_lbl, name_lbl)

        def _enter(_e, f=row, t=targets):
            f.config(bg="#F2F2F4")
            for lbl in t:
                lbl.config(bg="#F2F2F4")

        def _leave(_e, f=row, t=targets):
            f.config(bg="#FFFFFF")
            for lbl in t:
                lbl.config(bg="#FFFFFF")

        row.bind("<Enter>", _enter)
        row.bind("<Leave>", _leave)
        for w in targets:
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)

    def _build_records_chunk(self, build_id):
        """分批构建记录行（每批 12 行），页面即时响应、内容流式出现。"""
        if build_id != getattr(self, "_records_build_id", -1):
            return  # 已被新一次重建取代
        inner = self.records_inner
        pending = getattr(self, "_records_pending", [])
        chunk = 12
        i = getattr(self, "_records_chunk_i", 0)
        for g in pending[i:i + chunk]:
            try:
                self._build_record_row(inner, g)
            except Exception as exc:  # noqa: BLE001
                self._log_error("records_row", exc)
        self._records_chunk_i = i + chunk
        if self._records_chunk_i < len(pending):
            try:
                # 用 after(0) 而非 after_idle：每批之间让事件循环有机会重绘，页面边出现边加载
                self.root.after(0, lambda: self._build_records_chunk(build_id))
            except tk.TclError:
                pass

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
        except Exception:  # noqa: BLE001
            pass

    def _refresh_categories(self, start, end):
        cats = self.db.category_summary_between(start, end)
        apps = self.db.desktop_summary_between(start, end)
        sites = self.db.sites_summary_between(start, end)
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

    # ---------- 设置页 ----------
    def _build_settings(self):
        page = self.pages["settings"]
        sc = ScrollArea(page, bg=theme.BG, min_width=680)
        sc.pack(fill="both", expand=True)
        inner = sc.inner
        tk.Label(inner, text="设置", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(anchor="w", padx=theme.scale(28), pady=(theme.scale(32), theme.scale(16)))

        def section():
            f = tk.Frame(inner, bg=theme.BG)
            f.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(16)))
            return f

        # 板块1：基础采样参数（三行垂直独立）
        sec1 = section()
        self.var_interval = tk.StringVar(value=str(self.cfg.get("poll_interval_seconds", 1.0)))
        self.var_minsec = tk.StringVar(value=str(self.cfg.get("min_session_seconds", 3)))
        self.var_site = tk.BooleanVar(value=bool(self.cfg.get("browser_site_tracking", True)))

        def row(label, widget_factory):
            f = tk.Frame(sec1, bg=theme.BG)
            f.pack(fill="x", pady=4)
            f.grid_columnconfigure(0, minsize=theme.scale(130))
            tk.Label(f, text=label, font=theme.font(10), fg=theme.TEXT,
                     bg=theme.BG, anchor="w").grid(row=0, column=0, sticky="w")
            widget_factory(f).grid(row=0, column=1, sticky="w")

        row("采样间隔（秒）",
            lambda p: ttk.Spinbox(p, from_=0.5, to=10, increment=0.5,
                                  textvariable=self.var_interval, width=8))
        row("最短会话（秒）",
            lambda p: ttk.Spinbox(p, from_=1, to=120, increment=1,
                                  textvariable=self.var_minsec, width=8))
        row("网站识别",
            lambda p: ttk.Checkbutton(p, text="识别浏览器正在访问的网站",
                                      variable=self.var_site))

        # 板块2：排除进程
        sec2 = section()
        tk.Label(sec2, text="排除进程", font=theme.font(10), fg=theme.TEXT,
                 bg=theme.BG, width=14, anchor="w").pack(side="left")
        self.var_exclude = tk.StringVar(value="")
        ttk.Entry(sec2, textvariable=self.var_exclude, width=30).pack(side="left")
        RoundedButton(sec2, text="添加", width=64, height=26, radius=theme.CONTROL_RADIUS,
                      font=theme.font(9), command=self._add_exclude,
                      fill=theme.ACCENT, fg="#FFFFFF",
                      hover=theme.ACCENT_HOVER).pack(side="left", padx=(8, 0))
        self.chips_frame = tk.Frame(inner, bg=theme.BG)
        self.chips_frame.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(8)))
        self._rebuild_chips()

        # 板块3：功能按钮组
        sec3 = section()
        RoundedButton(sec3, text="保存设置", width=116, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), command=self._save_settings,
                      fill=theme.ACCENT, fg="#FFFFFF",
                      hover=theme.ACCENT_HOVER).pack(side="left", padx=theme.scale(4))
        RoundedButton(sec3, text="停止追踪", width=116, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), command=self._clear_tracking_lock,
                      fill=theme.SECONDARY_BG, fg=theme.TEXT).pack(side="left", padx=theme.scale(4))
        RoundedButton(sec3, text="退出程序", width=116, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), command=self._quit_app,
                      fill=theme.SECONDARY_BG, fg=theme.TEXT).pack(side="left", padx=theme.scale(4))

        # 板块4：数据文件
        sec5 = section()
        tk.Label(sec5, text="数据文件", font=theme.font(10, True), fg=theme.TEXT,
                 bg=theme.BG).pack(anchor="w")
        self.db_path_label = tk.Label(sec5, text=str(self.db.db_path),
                                      font=theme.font(9), fg=theme.SUB, bg=theme.BG)
        self.db_path_label.pack(anchor="w", pady=(4, 8))
        RoundedButton(sec5, text="打开数据目录", width=130, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), fill=theme.SECONDARY_BG, fg=theme.TEXT,
                      command=lambda: os.startfile(str(self.db.db_path.parent)),
                      bg=theme.BG).pack(anchor="w")

        # 板块5：当前前台窗口预览
        sec6 = section()
        tk.Label(sec6, text="当前前台窗口（仅预览，不参与计时）", font=theme.font(10, True),
                 fg=theme.TEXT, bg=theme.BG).pack(anchor="w")
        self.settings_now_label = tk.Label(sec6, text="--", font=theme.font(10),
                                           fg=theme.SUB, bg=theme.BG, justify="left")
        self.settings_now_label.pack(anchor="w", pady=6)

    def _save_settings(self):
        try:
            interval = float(self.var_interval.get())
            minsec = int(float(self.var_minsec.get()))
        except ValueError:
            messagebox.showwarning("设置", "采样间隔 / 最短会话请输入数字")
            return
        self.cfg["poll_interval_seconds"] = interval
        self.cfg["min_session_seconds"] = minsec
        self.cfg["browser_site_tracking"] = bool(self.var_site.get())
        cfg_path = project_root() / "config.json"
        cfg_path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        messagebox.showinfo("设置", "已保存，重启后台采集后生效")

    def _add_exclude(self):
        raw = self.var_exclude.get().replace("，", ",")
        added = False
        for part in raw.split(","):
            name = part.strip()
            if name and name not in self.cfg.get("exclude_processes", []):
                self.cfg.setdefault("exclude_processes", []).append(name)
                added = True
        self.var_exclude.set("")
        if added:
            self._rebuild_chips()
            self._persist_exclude()

    def _remove_exclude(self, name):
        self.cfg["exclude_processes"] = [
            x for x in self.cfg.get("exclude_processes", []) if x != name
        ]
        self._rebuild_chips()
        self._persist_exclude()

    def _rebuild_chips(self):
        for w in self.chips_frame.winfo_children():
            w.destroy()
        excludes = self.cfg.get("exclude_processes", [])
        if not excludes:
            tk.Label(self.chips_frame, text="未排除任何进程", font=theme.font(9),
                     fg=theme.SUB, bg=theme.BG).pack(side="left")
            return
        for name in excludes:
            chip = tk.Canvas(self.chips_frame, bg=theme.BG, highlightthickness=0, bd=0,
                             height=theme.scale(26))
            text = name + "  ×"

            def draw(c=chip, n=name):
                c.delete("all")
                w = c.winfo_width()
                h = c.winfo_height()
                if w <= 2:
                    return
                pts = rounded_polygon(1, 1, w - 1, h - 1, theme.scale(6))
                c.create_polygon(pts, smooth=True, fill=theme.ACCENT_LIGHTER, outline="")
                c.create_text(w / 2, h / 2, text=text, font=theme.font(9),
                              fill=theme.ACCENT)
                c.bind("<Button-1>", lambda _e, nn=n: self._remove_exclude(nn))

            chip.bind("<Configure>", lambda _e, c=chip, n=name: draw(c, n))
            chip.bind("<Map>", lambda _e, c=chip, n=name: draw(c, n))
            chip.pack(side="left", padx=(0, theme.scale(6)), pady=2)
            chip.configure(width=max(theme.scale(70), theme.scale(10) * len(name) + theme.scale(28)))

    def _persist_exclude(self):
        cfg_path = project_root() / "config.json"
        cfg_path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    def _clear_tracking_lock(self):
        lock_path = project_root() / self.cfg.get("data_dir", "data") / "tracking.lock"
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)
            messagebox.showinfo("停止追踪",
                                "已清除采集锁。若后台采集进程仍在运行，请到那个终端按 Ctrl+C 结束它。")
        else:
            messagebox.showinfo("停止追踪", "当前没有后台采集锁，无需处理。")

    def _refresh_settings_live(self, now: datetime):
        info = get_foreground_info()
        if info:
            text = f"{info['process']} · {info['title'] or '（无标题）'}"
            if info["category"] == "网站" and info.get("site"):
                text += f"  →  {info['site']}"
        else:
            text = "（当前环境拿不到前台窗口）"
        self.settings_now_label.config(text=f"{text}    [{now:%H:%M:%S}]")

    # ---------- 后台采集子进程 ----------
    def _spawn_background(self):
        """GUI 启动后自动拉起独立后台采集进程（start 模式；打包成 exe 后调用 exe 自身）。"""
        if self._background_running():
            return
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "start"]
            else:
                cmd = [sys.executable, "main.py", "start"]
            self._bg_proc = subprocess.Popen(
                cmd,
                cwd=str(project_root()),
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception as exc:  # noqa: BLE001
            print("自动启动后台采集失败:", exc)
            self._bg_proc = None

    def _stop_background_process(self):
        if self._bg_proc is None:
            return
        if self._bg_proc.poll() is None:
            try:
                self._bg_proc.terminate()
                self._bg_proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    self._bg_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._bg_proc = None

    # ---------- 系统托盘 ----------
    def _setup_tray(self):
        try:
            icon_dir = project_root() / self.cfg.get("data_dir", "data")
            icon_dir.mkdir(parents=True, exist_ok=True)
            self._tray_icon_path = icon_dir / "tray_icon.ico"
            if not tray.create_icon(self._tray_icon_path):
                return
            self._tray_enabled = tray.enable_tray(
                str(self._tray_icon_path),
                on_open=self._restore_from_tray,
                on_quit=self._quit_app,
                tooltip="屏幕使用时间 · 使用时长记录",
            )
        except Exception as exc:  # noqa: BLE001
            print("托盘初始化失败（将回退为任务栏最小化）:", exc)
            self._tray_enabled = False

    def _restore_from_tray(self):
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            self.root.title("屏幕使用时间")
        except tk.TclError:
            pass

    # ---------- 数据校验（只读） ----------
    def _check_overlap_sessions(self):
        try:
            n = self.db.conn.execute(
                "SELECT COUNT(*) FROM sessions a JOIN sessions b ON a.id < b.id "
                "AND a.start_time < b.end_time AND b.start_time < a.end_time"
            ).fetchone()[0]
        except Exception:
            return
        if n:
            self.root.after(800, lambda: messagebox.showwarning(
                "检测到重叠记录",
                f"数据库中存在 {n} 组时间重叠的会话，可能是之前同时运行过多个采集进程造成的重复统计。\n"
                "时长采集请只使用一个后台进程。"))

    def _background_running(self) -> bool:
        try:
            lock_path = project_root() / self.cfg.get("data_dir", "data") / "tracking.lock"
            if not lock_path.exists():
                return False
            pid_s = lock_path.read_text(encoding="utf-8").strip().split("|")[0]
            if not pid_s.isdigit():
                return False
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid_s))
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        except Exception:
            return False

    # ---------- 刷新循环 ----------
    def _refresh_loop(self):
        if not self.root.winfo_exists():
            return
        self.refresh()
        self.root.after(2000, self._refresh_loop)

    def _warm_stats(self):
        """启动后预渲染统计图，打开统计页时立即可见。"""
        try:
            if self._stats_figs is None:
                self._draw_stats()
        except Exception:  # noqa: BLE001
            pass

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
        except Exception:  # noqa: BLE001
            pass
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
        except Exception:  # noqa: BLE001
            pass

    def _hide_day_banner(self):
        try:
            self.day_banner.pack_forget()
        except Exception:  # noqa: BLE001
            pass

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

        # 各模块独立刷新，互不拖累（首页出错不影响分类 TOP3）
        try:
            self._refresh_home(start, now)
        except Exception as exc:  # noqa: BLE001
            self._log_error("home", exc)
        try:
            self._refresh_categories(start, now)
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
        """把界面刷新异常写入 data/ui_errors.log，便于排查 pythonw 下的静默错误。"""
        try:
            import traceback as _tb
            log_dir = project_root() / self.cfg.get("data_dir", "data")
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "ui_errors.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}: {exc}\n")
                f.write(_tb.format_exc() + "\n")
        except Exception:  # noqa: BLE001
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
