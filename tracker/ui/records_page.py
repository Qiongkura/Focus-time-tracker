"""详细记录页：按「小时 + 相同进程」合并后的会话列表，分批流式构建。

从 ``tracker/app.py`` 拆出。这一页依赖宿主的 ``db`` / ``pages["records"]`` /
``root`` / ``_period_start()`` / ``_exe_for_process()`` / ``_log_error()``，
这些仍由 ``ScreenTimeApp`` 提供。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox

from .. import theme
from ..utils import fmt_minsec
from ..widgets import (
    RoundedButton,
    ScrollArea,
    get_default_icon,
    get_display_name,
    get_exe_icon,
    get_globe_icon,
)
from .common import shorten, site_display

ROW_HEIGHT = 36

# 每批构建的行数：够小以保证界面即时响应，够大以避免回调过于频繁
RECORDS_CHUNK = 12


def group_sessions(rows, fallback_end: datetime) -> list[dict]:
    """把会话行按「小时 + 身份」合并为展示用记录行。"""
    groups: dict[tuple, dict] = {}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["start"])
        except Exception:  # noqa: BLE001
            dt = fallback_end
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
    return sorted(groups.values(),
                  key=lambda g: (g["start"], -g["seconds"]), reverse=True)


class RecordsPageMixin:
    """详细记录页的构建、合并、CSV 导出与分批渲染。"""

    def _build_records(self):
        page = self.pages["records"]
        sc = ScrollArea(page, bg=theme.BG, min_width=560)
        sc.pack(fill="both", expand=True)
        inner = sc.inner

        header = tk.Frame(inner, bg=theme.BG)
        header.pack(fill="x", padx=theme.scale(28), pady=(theme.scale(32), theme.scale(16)))
        tk.Label(header, text="详细记录", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(side="left")
        self._make_period_tabs(header)
        RoundedButton(
            header, text="导出 CSV", width=88, height=30, radius=6,
            font=theme.font(9), fill=theme.SECONDARY_BG, fg=theme.TEXT,
            bg=theme.BG, command=self._export_records_csv,
        ).pack(side="right", padx=(0, 8))

        self.records_list = tk.Frame(inner, bg=theme.BG)
        self.records_list.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(24)))
        self.records_inner = self.records_list
        self._records_sig = None
        self._records_built = False

    def _export_records_csv(self):
        try:
            start = self._period_start()
            now = datetime.now()
            out_dir = getattr(self, "report_dir", self.db.db_path.parent)
            out_path = out_dir / f"详细记录_{self.period}_{now:%Y%m%d_%H%M%S}.csv"
            count = self.db.export_sessions_csv(start, now, out_path)
            messagebox.showinfo("导出 CSV", f"已导出 {count} 条会话明细至：\n{out_path}")
        except Exception as exc:  # noqa: BLE001
            self._log_error("export_csv", exc)
            messagebox.showerror("导出失败", f"导出 CSV 失败：{exc}")

    def _refresh_records(self):
        if getattr(self, "_closing", False):
            return
        end = datetime.now()
        start = self._period_start()
        inner = self.records_inner
        try:
            if hasattr(self.db, "sessions_signature_between"):
                sig = self.db.sessions_signature_between(start, end)
            else:
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
        self._records_build_id = getattr(self, "_records_build_id", 0) + 1
        tid = getattr(self, "_records_chunk_timer", None)
        if tid:
            try:
                self.root.after_cancel(tid)
            except tk.TclError:
                pass
            self._records_chunk_timer = None
        for w in inner.winfo_children():
            w.destroy()
        rows = self.db.recent_sessions_between(start, end, limit=2000)
        if not rows:
            tk.Label(inner, text="暂无记录", font=theme.font(11), fg=theme.SUB,
                     bg=theme.BG).pack(pady=40)
            return
        ordered = group_sessions(rows, end)
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
            name = site_display(g)
            sub = ""
        else:
            exe = self._exe_for_process(g["process"])
            icon = get_exe_icon(exe) if exe else get_default_icon()
            name = get_display_name(g["process"], exe)
            sub = g["title"] if g["title"] else ""
        display = shorten(name, 26) + (f"（{shorten(sub, 20)}）" if sub else "")
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
        if getattr(self, "_closing", False):
            return
        if build_id != getattr(self, "_records_build_id", -1):
            return  # 已被新一次重建取代
        self._records_chunk_timer = None
        inner = self.records_inner
        pending = getattr(self, "_records_pending", [])
        chunk = RECORDS_CHUNK
        i = getattr(self, "_records_chunk_i", 0)
        for g in pending[i:i + chunk]:
            try:
                self._build_record_row(inner, g)
            except Exception as exc:  # noqa: BLE001
                self._log_error("records_row", exc)
        self._records_chunk_i = i + chunk
        if self._records_chunk_i < len(pending):
            try:
                self._records_chunk_timer = self.root.after(
                    0, lambda: self._build_records_chunk(build_id)
                )
            except tk.TclError:
                self._records_chunk_timer = None
