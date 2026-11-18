"""统计页：今日每小时使用 + 近 7 天趋势（matplotlib 懒加载）。

从 ``tracker/app.py`` 拆出。这一页的所有方法只依赖宿主的
``root`` / ``db`` / ``pages["stats"]`` / ``current_page``，以及本模块自己
维护的 ``_stats_*`` 状态，因此整体搬迁不影响任何外部调用点。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox

from .. import theme
from ..widgets import RoundedButton

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
            codes = [
                Path.MOVETO, Path.LINETO,
                Path.LINETO, Path.CURVE3, Path.CURVE3,
                Path.LINETO, Path.CURVE3, Path.CURVE3,
                Path.CLOSEPOLY,
            ]
            return Path(cp, codes)

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


class StatsPageMixin:
    """统计页的构建、重绘调度、图片报告导出与两张图表绘制。"""

    def _build_stats(self):
        page = self.pages["stats"]
        for w in page.winfo_children():
            w.destroy()
        page.configure(bg=theme.BG)

        header = tk.Frame(page, bg=theme.BG)
        header.pack(fill="x", padx=theme.scale(28), pady=(theme.scale(32), theme.scale(16)))
        tk.Label(header, text="统计", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(side="left")
        RoundedButton(
            header, text="导出报告图", width=96, height=30, radius=6,
            font=theme.font(9), fill=theme.SECONDARY_BG, fg=theme.TEXT,
            bg=theme.BG, command=self._export_stats_report,
        ).pack(side="right")

        self.stats_container = tk.Frame(page, bg=theme.BG)
        self.stats_container.pack(fill="both", expand=True, padx=theme.scale(24), pady=(0, theme.scale(24)))

        self._stats_figs = None
        self._stats_canvases = []
        self._stats_frames = []
        self._stats_fixed_size = None
        self._stats_drawing = False
        self._stats_redraw_timer = None
        self.stats_container.bind("<Configure>", self._stats_on_configure)

    def _export_stats_report(self):
        try:
            from ..report import generate_today_chart, generate_week_chart

            out_dir = getattr(self, "report_dir", self.db.db_path.parent)
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
            p_today = out_dir / f"今日使用_{stamp}.png"
            p_week = out_dir / f"近7天使用_{stamp}.png"
            generate_today_chart(self.db, p_today)
            generate_week_chart(self.db, p_week, days=7)
            messagebox.showinfo(
                "导出报告图",
                f"已生成今日与近 7 天统计图：\n• {p_today}\n• {p_week}",
            )
        except Exception as exc:  # noqa: BLE001
            self._log_error("export_report", exc)
            messagebox.showerror("导出失败", f"生成统计报告失败：{exc}")

    def _stats_on_configure(self, event):
        if getattr(self, "_closing", False) or self._stats_drawing or self.current_page != "stats":
            return
        container_w = event.width if event.width > 50 else None
        if container_w is None:
            return
        old_w = self._stats_fixed_size[0] if self._stats_fixed_size else None
        if old_w != container_w:
            self._stats_fixed_size = None
            if hasattr(self, "_stats_redraw_timer") and self._stats_redraw_timer:
                self.root.after_cancel(self._stats_redraw_timer)
            self._stats_redraw_timer = self.root.after(50, self._draw_stats)

    def _schedule_stats_draw(self):
        if getattr(self, "_closing", False) or self._stats_drawing:
            return
        if self.current_page != "stats":
            return
        self._stats_fixed_size = None
        self._draw_stats(force=True)

    def _ensure_stats_canvases(self):
        if self._stats_figs is None:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            from ..report import setup_cjk_font

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
        if getattr(self, "_closing", False) or self._stats_drawing:
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
        fig_w = int(max(theme.scale(480), container_w - theme.scale(8)))
        avail_h = int(root_h - theme.scale(96))
        total_h = max(int(200 * factor), avail_h - 110)
        heights = (int(total_h * 0.56), int(total_h * 0.42))
        self._stats_fixed_size = (fig_w, heights)
        for i, (fig, canvas) in enumerate(zip(figs, canvases, strict=False)):
            fig_h = max(int(120 * factor), heights[i])
            fig.set_size_inches(fig_w / 100.0, fig_h / 100.0)
            tk_widget = canvas.get_tk_widget()
            tk_widget.config(width=fig_w, height=fig_h)
            tk_widget.update_idletasks()
        self._draw_hourly(figs[0])
        self._draw_week(figs[1])
        for canvas in canvases:
            canvas.draw_idle()
            canvas.get_tk_widget().update_idletasks()

    def _hourly_usage(self, start, end):
        if hasattr(self.db, "hourly_sessions_between"):
            rows = self.db.hourly_sessions_between(start, end)
        else:
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
        max_total = max(bottom) if bottom else 0
        for h, total in enumerate(bottom):
            if total > 0.5:
                ax.text(h, total + max_total * 0.02, f"{total:.0f}",
                        ha="center", va="bottom", fontsize=8, color=theme.SUB)
        ax.set_ylim(0, max_total * 1.16 + 2)
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
        max_total = max(bottom) if bottom else 0
        for i, total in enumerate(bottom):
            if total > 0.05:
                ax.text(i, total + max_total * 0.02, f"{total:.1f}",
                        ha="center", va="bottom", fontsize=9, color=theme.SUB)
        if max_total > 0:
            ax.set_ylim(0, max_total * 1.16 + 0.3)
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
            fig = ax.figure
            fig_w, fig_h = fig.get_size_inches()
            dpi = fig.dpi
            sp = fig.subplotpars
            axes_w = fig_w * dpi * (sp.right - sp.left)
            axes_h = fig_h * dpi * (sp.top - sp.bottom)
            px_x = axes_w / max(1e-6, xlim[1] - xlim[0])
            px_y = axes_h / max(1e-6, ylim[1] - ylim[0])
            mutation_aspect = px_x / px_y
            r_data = radius_px / max(1e-6, px_x)
        except Exception:  # noqa: BLE001
            mutation_aspect = 1.0
            r_data = radius_px
        if top and bottom:
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
