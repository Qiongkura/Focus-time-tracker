"""首页：今日/本周概览卡片、迷你趋势与时段筛选。

从 ``tracker/app.py`` 拆出。``_period_start()`` / ``_exe_for_process()`` /
``_make_period_tabs()`` 定义在这里，供记录页、分类页、刷新控制器复用。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

from .. import theme
from ..overrides import override_for
from ..utils import fmt_minsec
from ..widgets import (
    FlowFrame,
    ProgressBar,
    RoundedButton,
    RoundedCard,
    ScrollArea,
    get_default_icon,
    get_display_name,
    get_exe_icon,
    get_globe_icon,
    resolve_exe_path,
)
from .common import shorten, site_display

MAX_ROWS = 5

# 行内容的最小可用宽度（逻辑像素）：再窄就会挤到名称/时长，不如给个下限
CONTENT_MIN_WIDTH = 280
# 名称列预留宽度：图标列 + 右侧时长列
NAME_RESERVE = 150
# 重建判据的宽度量化步长
WIDTH_QUANTIZE_STEP = 24


class HomePageMixin:
    """首页概览卡片与时段切换。"""

    # ---------- 尺寸换算 ----------

    def _content_width(self, card_width: float) -> int:
        """卡片宽度 -> 行内容可用宽度（两侧各留 16 逻辑像素）。"""
        return int(max(theme.scale(CONTENT_MIN_WIDTH), card_width - theme.scale(32)))

    @staticmethod
    def _quantize_width(width: int) -> int:
        step = max(1, int(theme.scale(WIDTH_QUANTIZE_STEP)))
        return int(width // step) * step

    def _make_period_tabs(self, parent) -> dict[str, RoundedButton]:
        """创建跨页面同步的「今日 / 本周」切换按钮组。"""
        tabs = tk.Frame(parent, bg=theme.BG)
        tabs.pack(side="right")
        group: dict[str, RoundedButton] = {}
        for t in ("今日", "本周"):
            btn = RoundedButton(tabs, text=t, width=72, height=30, radius=6,
                                selectable=True, selected=(t == self.period),
                                font=theme.font(10), fill=theme.ACCENT, fg="#FFFFFF",
                                bg=theme.BG, command=lambda t=t: self.set_period(t))
            btn.pack(side="left", padx=2)
            group[t] = btn
        groups = getattr(self, "_period_btn_groups", None)
        if groups is None:
            self._period_btn_groups = [group]
        else:
            groups.append(group)
        return group

    # ---------- 首页 ----------
    def _build_home(self):
        page = self.pages["home"]
        sc = ScrollArea(page, bg=theme.BG, min_width=380, respect_req=False)
        sc.pack(fill="both", expand=True)
        inner = sc.inner

        header = tk.Frame(inner, bg=theme.BG)
        header.pack(fill="x", padx=theme.scale(28), pady=(theme.scale(32), 0))
        tk.Label(header, text="概览", font=theme.font(22, True),
                 fg=theme.TEXT_TITLE, bg=theme.BG).pack(side="left")
        self.tab_buttons = self._make_period_tabs(header)

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

        # 注意：FlowFrame.__init__ 内部会执行 theme.scale()，此处传逻辑像素，避免高分屏双重放大
        mini_row = FlowFrame(inner, bg=theme.BG, item_width=210, item_height=92, gap=12)
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
        card_w = (w - theme.scale(16)) / 2.0
        mode = "side" if card_w >= theme.scale(300) else "stack"
        if mode == self._home_card_mode:
            return
        self._home_card_mode = mode

        for c in (self.app_card, self.site_card):
            c.grid_forget()
        if mode == "side":
            self.app_card.grid(row=0, column=0, sticky="nsew",
                               padx=theme.scale(8), pady=theme.scale(4))
            self.site_card.grid(row=0, column=1, sticky="nsew",
                                padx=theme.scale(8), pady=theme.scale(4))
            cards.grid_columnconfigure(0, weight=1, uniform="hcard")
            cards.grid_columnconfigure(1, weight=1, uniform="hcard")
        else:
            cards.grid_columnconfigure(1, weight=0, uniform="", minsize=0)
            cards.grid_columnconfigure(0, weight=1, uniform="", minsize=0)
            self.app_card.grid(row=0, column=0, sticky="ew", pady=theme.scale(6))
            self.site_card.grid(row=1, column=0, sticky="ew", pady=theme.scale(6))
        try:
            self.root.after_idle(self._refresh_card_widths)
        except tk.TclError:
            pass

    def set_period(self, period: str):
        self.period = period
        for group in getattr(self, "_period_btn_groups", [self.tab_buttons]):
            for t, btn in group.items():
                btn.set_selected(t == period)
        self._records_sig = None
        self._records_dirty = True
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
            cache = getattr(self, "_db_exe_cache", None)
            if isinstance(cache, dict):
                cache[process] = exe
            return exe
        cache = getattr(self, "_db_exe_cache", None)
        if isinstance(cache, dict) and process in cache:
            return cache[process]
        try:
            if hasattr(self.db, "latest_exe_path"):
                found = self.db.latest_exe_path(process)
            else:
                row = self.db.conn.execute(
                    "SELECT exe_path FROM sessions WHERE process=? AND exe_path<>'' "
                    "ORDER BY id DESC LIMIT 1", (process,)).fetchone()
                found = row[0] if row and row[0] else ""
            if isinstance(cache, dict):
                cache[process] = found
            return found
        except Exception as exc:  # noqa: BLE001
            self._log_error("exe_lookup", exc)
        return ""

    def _process_item(self, a, full_name=False):
        exe = self._exe_for_process(a["process"])
        name = get_display_name(a["process"], exe)
        icon = get_exe_icon(exe, 40) if exe else get_default_icon(40)
        return {"icon": icon,
                "name": name if full_name else shorten(name, 18),
                "seconds": a["seconds"],
                "category": a["category"],
                "process": a["process"]}

    def _site_item(self, s, full_name=False):
        return {"icon": get_globe_icon(40),
                "name": (site_display(s) if full_name else shorten(site_display(s), 18)),
                "seconds": s["seconds"], "category": "网站"}

    def _sync_card(self, card, card_key, items, total, fill, bar_color,
                   show_pct=False, full_name=False, actions=False):
        self._card_widgets[card_key] = card
        self._card_payload[card_key] = (items, total, fill, bar_color,
                                        show_pct, full_name, actions)
        if card_key not in self._card_bound:
            self._card_bound.add(card_key)
            card.bind("<Configure>", lambda _e, k=card_key: self._on_card_resize(k), add="+")

        w = card.winfo_width()
        if w > 10:
            self._card_widths[card_key] = w
        else:
            w = self._card_widths.get(card_key, theme.scale(640))
        width = self._content_width(w)
        sig = (self._quantize_width(width), [it["name"] for it in items])
        if self._rows_sig.get(card_key) != sig:
            try:
                card.delete("rowwin")
                self._build_card_rows(card, card_key, items, fill, bar_color,
                                      width, show_pct, full_name, actions)
                self._rows_sig[card_key] = sig
            except Exception:
                self._card_rows[card_key] = []
                raise
            self._update_card_rows(card_key, items, total, show_pct, full_name)
        else:
            self._update_card_rows(card_key, items, total, show_pct, full_name)

    def _on_card_resize(self, card_key):
        if getattr(self, "_closing", False):
            return
        try:
            self._apply_card_widths(card_key)
        except Exception as exc:  # noqa: BLE001
            self._log_error("card_resize", exc)

    def _apply_card_widths(self, card_key):
        rows = self._card_rows.get(card_key)
        card = self._card_widgets.get(card_key)
        if not rows or card is None:
            return
        w = card.winfo_width()
        if w <= 10:
            return
        width = self._content_width(w)
        if self._card_applied_width.get(card_key) == width:
            return
        self._card_applied_width[card_key] = width
        self._card_widths[card_key] = w

        name_area = max(theme.scale(120), width - theme.scale(NAME_RESERVE))
        y = theme.scale(54)
        for info in rows:
            if info.get("name_area") != name_area:
                info["name_area"] = name_area
                info["name"].config(wraplength=name_area)
                info["row_h"] = (max(theme.scale(58),
                                     info["name"].winfo_reqheight() + theme.scale(36))
                                 + info["extra_h"])
            if info.get("applied_row_h") != info["row_h"]:
                info["applied_row_h"] = info["row_h"]
                info["row"].configure(height=info["row_h"])
            card.itemconfigure(info["win"], width=width)
            card.coords(info["win"], theme.scale(16), y)
            y += info["row_h"] + theme.scale(6)
        height = y + theme.scale(10)
        self._card_heights[card_key] = height
        if card_key in self._card_autosize and card.winfo_height() != int(height):
            card.configure(height=int(height))

    def _refresh_card_widths(self):
        if getattr(self, "_closing", False):
            return
        for card_key in list(self._card_rows):
            try:
                self._apply_card_widths(card_key)
            except Exception as exc:  # noqa: BLE001
                self._log_error("card_resize", exc)

    def _build_card_rows(self, card, card_key, items, fill, bar_color, width,
                         show_pct=False, full_name=False, actions=False):
        rows = []
        if not items:
            items = [{"icon": None, "name": "今日暂无记录", "seconds": 0, "category": ""}]
        y = theme.scale(54)
        name_area = max(theme.scale(120), width - theme.scale(NAME_RESERVE))
        for it in items:
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
                row_h = max(theme.scale(58), name_lbl.winfo_reqheight() + theme.scale(36)) + extra_h
                row.configure(height=row_h)
                row.grid_propagate(False)
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
                row.grid_columnconfigure(1, weight=1)
                win = card.create_window(theme.scale(16), y, window=row, anchor="nw",
                                         width=width, tags="rowwin")
                rows.append({"icon": icon_lbl, "name": name_lbl, "bar": bar,
                             "time": time_lbl, "actions": actions_frame,
                             "row": row, "win": win, "extra_h": extra_h,
                             "name_area": name_area, "row_h": row_h,
                             "applied_row_h": row_h})
                y += row_h + theme.scale(6)
            except Exception:
                continue
        self._card_rows[card_key] = rows
        self._card_applied_width[card_key] = width
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
        has_game = any(a["category"] == "游戏" for a in apps)
        app_total = sum(a["seconds"] for a in apps)
        site_total = sum(s["seconds"] for s in sites)
        suffix = "（含游戏）" if has_game else ""
        self._card_total_labels["应用"].config(text=f"共 {fmt_minsec(app_total)}{suffix}")
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

        pure_apps = [a for a in apps if a["category"] == "应用"]
        game_apps = [a for a in apps if a["category"] == "游戏"]

        def friendly(proc: str) -> str:
            return get_display_name(proc, self._exe_for_process(proc)) or proc

        top_any_desktop = friendly(apps[0]["process"]) if apps else "--"
        top_pure_app = friendly(pure_apps[0]["process"]) if pure_apps else "--"
        top_game = friendly(game_apps[0]["process"]) if game_apps else "--"
        top_site = site_display(sites[0]) if sites else "--"
        prefix = "今日" if self.period == "今日" else "本周"
        ratio_label = f"占{prefix}比例"

        if self.filter == "全部":
            values = [
                (fmt_minsec(total_all), f"{prefix}总时长"),
                (top_any_desktop, "最常用桌面程序"),
                (top_site, "最常访问网站"),
                (fmt_minsec(total_game), "游戏时长"),
            ]
        elif self.filter == "网站":
            pct = f"{total_site / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_site), f"{prefix}网站时长"),
                (top_site, "最常访问网站"),
                (f"{len(sites)} 个", "访问站点（去重）"),
                (pct, ratio_label),
            ]
        elif self.filter == "游戏":
            pct = f"{total_game / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_game), f"{prefix}游戏时长"),
                (top_game, "最常玩游戏"),
                (f"{len(game_apps)} 个", "游戏数量（去重）"),
                (pct, ratio_label),
            ]
        else:
            pct = f"{total_app / total_all * 100:.0f}%" if total_all > 0 else "--"
            values = [
                (fmt_minsec(total_app), f"{prefix}纯应用时长"),
                (top_pure_app, "最常用软件"),
                (f"{len(pure_apps)} 个", "软件数量（去重）"),
                (pct, ratio_label),
            ]
        for (card, value_lbl, caption_lbl), (value, caption) in zip(
                self.mini_cards, values, strict=False):
            value_lbl.config(text=str(value))
            caption_lbl.config(text=caption)
            try:
                need = max(value_lbl.winfo_reqwidth(), caption_lbl.winfo_reqwidth())
                card.configure(width=max(theme.scale(240), need + theme.scale(28)))
            except tk.TclError:
                pass
        try:
            self.mini_row._relayout()
        except Exception as exc:  # noqa: BLE001
            self._log_error("ui", exc)
