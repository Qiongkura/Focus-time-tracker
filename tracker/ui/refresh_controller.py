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
# 内容宽度「精确落位」的防抖。重排整块内容区是最贵的一步（首页约 85 个
# 控件要重绘），所以比布局收尾更晚、且只在真正停手后才做；拖动过程中由
# ScrollArea 自己的节流负责跟手。
RESIZE_CONTENT_SETTLE_MS = 280


class RefreshMixin:
    """定时刷新、页面重绘调度与跨天提示。"""

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
        # 拖窗口时 <Configure> 每秒能触发几十次，而一次完整 refresh() 要跑
        # 二十来条 SQL。原实现每次都 after_idle 一个完整 refresh()，事件积压
        # 起来就表现为「时间条适配慢」。
        #
        # 现在拆成两层：
        #  * 拖动过程中——卡片自己的 <Configure> 触发 _apply_card_widths，
        #    只改行宽/换行/坐标，不建控件不查库，每帧跟手；
        #  * 停手之后——防抖跑一次布局收尾（_on_resize_settled），
        #    处理并排 <-> 竖排翻转，同样不查库。
        try:
            if getattr(self, "_resize_timer", None):
                self.root.after_cancel(self._resize_timer)
            self._resize_timer = self.root.after(RESIZE_DEBOUNCE_MS,
                                                 self._on_resize_settled)
        except tk.TclError:
            pass
        # 内容宽度精确落位用更长的防抖单独安排：它比布局收尾贵一个数量级，
        # 如果跟着 90ms 的防抖走，拖动中几乎每帧都会触发一次，节流就白做了
        try:
            if getattr(self, "_content_timer", None):
                self.root.after_cancel(self._content_timer)
            self._content_timer = self.root.after(RESIZE_CONTENT_SETTLE_MS,
                                                  self._on_content_settled)
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

    def _on_resize_settled(self):
        """缩放停手后的**布局**收尾（内容宽度重排见 ``_on_content_settled``）。

        这里**不调用** ``refresh()``：窗口尺寸变化只影响布局，数据一点没变，
        重新查库纯属浪费。一次完整刷新要跑二十来条 SQL 加图标查询，而拖拽
        过程中 <Configure> 来得比这快得多，一旦在缩放路径上跑完整刷新就会
        形成「慢帧 -> 定时器到期 -> 完整刷新 -> 更慢」的雪球，这正是
        「时间条适配慢」的真正原因。

        数据更新交给原本每 2 秒一次的 ``_refresh_loop``，缩放只做布局。
        """
        self._resize_timer = None
        try:
            # 布局模式（并排 <-> 竖排）可能翻转，这一步只在真翻转时动 grid，
            # 所以可以保持 90ms 的响应速度
            self._relayout_home_cards()
            self._relayout_categories()
        except Exception as exc:  # noqa: BLE001
            self._log_error("resize_relayout", exc)
        try:
            self.root.after_idle(self._refresh_card_widths)
        except tk.TclError:
            pass

    def _on_content_settled(self):
        """内容宽度精确落位。

        拖动过程中内容宽度是「量化 + 限流」跟进的，可能停在离目标不足一个
        量化步长的位置；真正停手后在这里补一次精确值，保证缩到最小时右侧
        不留误差（也不会因为差几个像素而被裁）。
        """
        self._content_timer = None
        try:
            force_all_content_resize()
        except Exception as exc:  # noqa: BLE001
            self._log_error("resize_content", exc)
        # 内容宽度变了，卡片宽度要等几何计算落地后才拿得到，放进 idle
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
            self._card_applied_width.clear()
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
