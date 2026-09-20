"""详细记录页：按「小时 + 相同进程」合并后的会话列表，分批流式构建。

从 ``tracker/app.py`` 拆出。这一页依赖宿主的 ``db`` / ``pages["records"]`` /
``root`` / ``_period_start()`` / ``_exe_for_process()`` / ``_log_error()``，
这些仍由 ``ScreenTimeApp`` 提供。
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta

from .. import theme
from ..utils import fmt_minsec
from ..widgets import (
    ScrollArea, get_default_icon, get_display_name, get_exe_icon, get_globe_icon,
)
from .common import shorten, site_display

ROW_HEIGHT = 36

# 每批构建的行数：够小以保证界面即时响应，够大以避免回调过于频繁
RECORDS_CHUNK = 12


def group_sessions(rows, fallback_end: datetime) -> list[dict]:
    """把会话行按「小时 + 身份」合并为展示用记录行。

    - 身份：网站按站点合并，其余按进程名合并；
    - 同一小时内反复出现的同一身份只保留一行，时长累加、次数累加；
    - 标题/站点取该小时内时长最长的那次（``best`` 用 ``>=`` 更新，
      所以并列时保留后出现的一条）。

    排序：按 ``start`` 倒序（最近的小时在前）。注意 ``key`` 里的
    ``-seconds`` 配合 ``reverse=True`` 后会被再次反转，因此 ``start``
    相同的分组实际是「时长升序」——这是拆分前就有的行为，这里原样保留。

    纯函数，不碰数据库也不碰 Tk，便于单测。
    """
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
    """详细记录页的构建、合并与分批渲染。"""

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
        ordered = group_sessions(rows, end)
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
                # 用 after(0) 而非 after_idle：每批之间让事件循环有机会重绘，页面边出现边加载
                self.root.after(0, lambda: self._build_records_chunk(build_id))
            except tk.TclError:
                pass
