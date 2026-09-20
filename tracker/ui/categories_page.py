"""分类页：恒三栏横排，窄窗横向滚动，支持把进程改判到别的分类。

从 ``tracker/app.py`` 拆出。分类改动通过 ``tracker.overrides`` 持久化。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime

from .. import theme
from ..overrides import remove_override, set_override
from ..utils import fmt_minsec
from ..widgets import RoundedCard, ScrollArea


class CategoriesPageMixin:
    """分类页的三栏布局与进程改判。"""

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
        """分类三卡：横向放得下就三栏并排，放不下自动竖排。

        只在布局模式真正切换时才动 grid；重复调用直接返回，避免
        每次缩放都 grid_forget + grid 引发 <Configure> 级联重绘。
        """
        w = self.cat_grid.winfo_width()
        if w <= 10:
            return
        # 卡片内容需要至少 360 逻辑宽度，三栏阈值相应提高，保证不裁内容
        mode = "side" if w >= theme.scale(360) * 3 + theme.scale(16) else "stack"
        if mode == self._cat_card_mode:
            return
        self._cat_card_mode = mode

        cards = list(self.cat_cards.values())
        for c in cards:
            c["card"].grid_forget()
        if mode == "side":
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
            self._card_autosize.add(key)
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
