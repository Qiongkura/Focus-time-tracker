"""刷新调度：主刷新循环、统计图预热、跨 0 点提示与窗口尺寸响应。

从 ``tracker/app.py`` 拆出。``refresh()`` 是各页面统一的数据入口，
由 ``_refresh_loop`` 定时调用。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta

from .. import theme
from ..widgets import force_all_content_resize

# 窗口缩放防抖：拖动过程中不做重活，停手后等这么久再跑一次布局收尾
RESIZE_DEBOUNCE_MS = 90
# 内容宽度「精确落位」的防抖
RESIZE_CONTENT_SETTLE_MS = 280


class RefreshMixin:
    """定时刷新、页面重绘调度与跨天提示。"""

    # ---------- 刷新循环 ----------
    def _refresh_loop(self):
        self._refresh_timer = None
        if getattr(self, "_closing", False) or getattr(self, "_hidden_to_tray", False):
            return
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        # 防御：refresh 内部任何未预期异常都不能中断 after 链，
        # 否则界面会永久冻结（只显示最后一次成功刷新的内容）
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self._log_error("refresh_loop", exc)
        if getattr(self, "_closing", False) or getattr(self, "_hidden_to_tray", False):
            return
        try:
            self._refresh_timer = self.root.after(2000, self._refresh_loop)
        except tk.TclError:
            pass

    def _warm_stats(self):
        """启动后预渲染统计图，打开统计页时立即可见。"""
        if getattr(self, "_closing", False) or getattr(self, "_hidden_to_tray", False):
            return
        try:
            if self._stats_figs is None:
                self._draw_stats()
        except Exception as exc:  # noqa: BLE001
            self._log_error("ui", exc)

    def _schedule_hourly_stats(self):
        """整点刷新统计图（补上上一小时的数据）。"""
        if getattr(self, "_closing", False):
            return
        try:
            now = datetime.now()
            nxt = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            delay_ms = int((nxt - now).total_seconds() * 1000) + 500
            self._hourly_timer = self.root.after(delay_ms, self._on_hourly_stats)
        except tk.TclError:
            pass

    def _on_hourly_stats(self):
        if getattr(self, "_closing", False):
            return
        try:
            if not getattr(self, "_hidden_to_tray", False) and self._stats_figs is not None:
                self._draw_stats()
        except Exception as exc:  # noqa: BLE001
            self._log_error("ui", exc)
        self._schedule_hourly_stats()

    def _on_root_resize(self):
        if getattr(self, "_closing", False):
            return
        try:
            if getattr(self, "_resize_timer", None):
                self.root.after_cancel(self._resize_timer)
            self._resize_timer = self.root.after(RESIZE_DEBOUNCE_MS,
                                                 self._on_resize_settled)
        except tk.TclError:
            pass
        try:
            if getattr(self, "_content_timer", None):
                self.root.after_cancel(self._content_timer)
            self._content_timer = self.root.after(RESIZE_CONTENT_SETTLE_MS,
                                                  self._on_content_settled)
        except tk.TclError:
            pass
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

    def _on_resize_settled(self):
        """缩放停手后的布局收尾（不重新查库）。"""
        self._resize_timer = None
        if getattr(self, "_closing", False):
            return
        try:
            self._relayout_home_cards()
            self._relayout_categories()
        except Exception as exc:  # noqa: BLE001
            self._log_error("resize_relayout", exc)
        try:
            self.root.after_idle(self._refresh_card_widths)
        except tk.TclError:
            pass

    def _on_content_settled(self):
        """内容宽度精确落位。"""
        self._content_timer = None
        if getattr(self, "_closing", False):
            return
        try:
            force_all_content_resize()
        except Exception as exc:  # noqa: BLE001
            self._log_error("resize_content", exc)
        try:
            self.root.after_idle(self._refresh_card_widths)
        except tk.TclError:
            pass

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
            self._log_error("ui", exc)

    def _hide_day_banner(self):
        if getattr(self, "_closing", False):
            return
        try:
            self.day_banner.pack_forget()
        except Exception as exc:  # noqa: BLE001
            self._log_error("ui", exc)

    def refresh(self):
        if getattr(self, "_closing", False):
            return
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
            self._card_applied_width.clear()
            self._records_sig = None
            self._records_dirty = True
            self._stats_fixed_size = None
            self._show_day_banner()
        self._last_refresh_date = today

        # 按需刷新当前可见页：后台未打开的页面不在每 2 秒周期里空转重算 UI，
        # 切页时由 show_page() 立即补刷一次。
        page = getattr(self, "current_page", "home")
        if page in ("home", "categories"):
            try:
                snap = self._period_snapshot(start, now)
            except Exception as exc:  # noqa: BLE001
                self._log_error("snapshot", exc)
                snap = None
            if page == "home":
                try:
                    self._refresh_home(start, now, snapshot=snap)
                except Exception as exc:  # noqa: BLE001
                    self._log_error("home", exc)
            else:
                try:
                    self._refresh_categories(start, now, snapshot=snap)
                except Exception as exc:  # noqa: BLE001
                    self._log_error("categories", exc)
        elif page == "settings":
            try:
                self._refresh_settings_live(now)
            except Exception as exc:  # noqa: BLE001
                self._log_error("settings_live", exc)
        elif page == "records":
            try:
                self._records_dirty = False
                self._refresh_records()
            except Exception as exc:  # noqa: BLE001
                self._log_error("records", exc)

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
