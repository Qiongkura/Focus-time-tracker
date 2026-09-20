"""设置页：采样参数、排除进程、数据文件与前台窗口预览。

从 ``tracker/app.py`` 拆出。这一页依赖宿主的 ``cfg`` / ``db`` / ``pages`` /
``_preview``（后台采样线程）以及 ``_quit_app()``。
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from .. import theme
from ..config import project_root
from ..widgets import RoundedButton, ScrollArea, rounded_polygon
from .common import wrap_to_area


class SettingsPageMixin:
    """设置页的构建、保存与排除进程管理。"""

    # ---------- 设置页 ----------
    def _build_settings(self):
        page = self.pages["settings"]
        # 同 records 页：min_width 不能超过最小窗口的可用宽度（760 - 160 = 600），
        # 否则缩到最小时右侧内容要横向滚动才能看到
        sc = ScrollArea(page, bg=theme.BG, min_width=560)
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
        wrap_to_area(self.db_path_label, sc)
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
        # 窗口标题长度不可控，不换行会把整页内容撑宽
        wrap_to_area(self.settings_now_label, sc)

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
        # 只读后台线程的缓存结果：主线程不再直接调用 get_foreground_info()，
        # 浏览器历史库被独占时也不会把设置页（乃至整个界面）拖住
        ready, info = self._preview.latest()
        if not ready:
            text = "正在获取前台窗口…"
        elif info:
            text = f"{info['process']} · {info['title'] or '（无标题）'}"
            if info["category"] == "网站" and info.get("site"):
                text += f"  →  {info['site']}"
        else:
            text = "（当前环境拿不到前台窗口）"
        self.settings_now_label.config(text=f"{text}    [{now:%H:%M:%S}]")
