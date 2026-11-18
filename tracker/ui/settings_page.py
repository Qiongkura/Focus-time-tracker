"""设置页：采样参数、排除进程、隐私保留、开机自启与数据维护。

从 ``tracker/app.py`` 拆出。这一页依赖宿主的 ``cfg`` / ``db`` / ``pages`` /
``_preview``（后台采样线程）以及 ``_quit_app()``。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .. import theme
from ..config import normalize_config, project_root
from ..lock import LockError, TrackingLock
from ..privacy import UrlPolicy, apply_retention
from ..widgets import RoundedButton, ScrollArea, rounded_polygon
from .common import wrap_to_area

_AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_REG_NAME = "FocusTimeTracker"


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe_path = Path(sys.executable)
    pythonw = exe_path.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else exe_path
    main_py = project_root() / "main.py"
    return f'"{runner}" "{main_py}" dashboard --minimized'


class SettingsPageMixin:
    """设置页的构建、保存、排除进程、开机自启与数据维护。"""

    # ---------- 开机自启注册表助手 ----------
    @staticmethod
    def _is_autostart_enabled() -> bool:
        if os.name != "nt":
            return False
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_PATH, 0,
                                winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, _AUTOSTART_REG_NAME)
                return bool(val)
        except OSError:
            return False

    @staticmethod
    def _set_autostart(enabled: bool) -> None:
        if os.name != "nt":
            raise OSError("仅 Windows 支持注册表开机自启动")
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_PATH, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _AUTOSTART_REG_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, _AUTOSTART_REG_NAME)
                except FileNotFoundError:
                    pass

    # ---------- 设置页 ----------
    def _build_settings(self):
        page = self.pages["settings"]
        sc = ScrollArea(page, bg=theme.BG, min_width=560)
        sc.pack(fill="both", expand=True)
        inner = sc.inner
        tk.Label(inner, text="设置", font=theme.font(22, True), fg=theme.TEXT_TITLE,
                 bg=theme.BG).pack(anchor="w", padx=theme.scale(28),
                                   pady=(theme.scale(32), theme.scale(16)))

        def section():
            f = tk.Frame(inner, bg=theme.BG)
            f.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(16)))
            return f

        # 板块1：采样、空闲与隐私保留参数
        sec1 = section()
        self.var_interval = tk.StringVar(value=str(self.cfg.get("poll_interval_seconds", 1.0)))
        self.var_minsec = tk.StringVar(value=str(self.cfg.get("min_session_seconds", 3)))
        self.var_idle = tk.StringVar(value=str(self.cfg.get("idle_timeout_seconds", 0)))
        self.var_retention = tk.StringVar(value=str(self.cfg.get("retention_days", 0)))
        self.var_site = tk.BooleanVar(value=bool(self.cfg.get("browser_site_tracking", True)))
        self.var_strip_query = tk.BooleanVar(value=bool(self.cfg.get("strip_url_query", True)))
        self.var_full_url = tk.BooleanVar(value=bool(self.cfg.get("store_full_url", True)))
        self.var_autostart = tk.BooleanVar(value=self._is_autostart_enabled())

        def row(label, widget_factory, hint: str = ""):
            f = tk.Frame(sec1, bg=theme.BG)
            f.pack(fill="x", pady=3)
            f.grid_columnconfigure(0, minsize=theme.scale(130))
            tk.Label(f, text=label, font=theme.font(10), fg=theme.TEXT,
                     bg=theme.BG, anchor="w").grid(row=0, column=0, sticky="w")
            widget_factory(f).grid(row=0, column=1, sticky="w")
            if hint:
                tk.Label(f, text=hint, font=theme.font(9), fg=theme.SUB,
                         bg=theme.BG).grid(row=0, column=2, sticky="w", padx=(8, 0))

        row("采样间隔（秒）",
            lambda p: ttk.Spinbox(p, from_=0.5, to=10, increment=0.5,
                                  textvariable=self.var_interval, width=8))
        row("最短会话（秒）",
            lambda p: ttk.Spinbox(p, from_=1, to=120, increment=1,
                                  textvariable=self.var_minsec, width=8))
        row("空闲暂停（秒）",
            lambda p: ttk.Spinbox(p, from_=0, to=3600, increment=60,
                                  textvariable=self.var_idle, width=8),
            hint="0 = 不启用（如 300 表示 5 分钟）")
        row("历史保留（天）",
            lambda p: ttk.Spinbox(p, from_=0, to=3650, increment=30,
                                  textvariable=self.var_retention, width=8),
            hint="0 = 永久保留")
        row("网站识别",
            lambda p: ttk.Checkbutton(p, text="识别浏览器正在访问的具体网站",
                                      variable=self.var_site))
        row("URL 隐私",
            lambda p: ttk.Checkbutton(p, text="去掉 URL 查询参数 (?query 与 #fragment)",
                                      variable=self.var_strip_query))
        row("URL 粒度",
            lambda p: ttk.Checkbutton(p, text="保留页面路径（取消则仅记录协议与域名）",
                                      variable=self.var_full_url))
        row("开机自启",
            lambda p: ttk.Checkbutton(p, text="登录 Windows 时自动后台启动（静默驻留托盘）",
                                      variable=self.var_autostart,
                                      command=self._on_toggle_autostart))

        # 板块2：排除进程
        sec2 = section()
        tk.Label(sec2, text="排除进程", font=theme.font(10), fg=theme.TEXT,
                 bg=theme.BG, width=12, anchor="w").pack(side="left")
        self.var_exclude = tk.StringVar(value="")
        ttk.Entry(sec2, textvariable=self.var_exclude, width=24).pack(side="left")
        RoundedButton(sec2, text="添加", width=64, height=26, radius=theme.CONTROL_RADIUS,
                      font=theme.font(9), command=self._add_exclude,
                      fill=theme.ACCENT, fg="#FFFFFF",
                      hover=theme.ACCENT_HOVER).pack(side="left", padx=(8, 0))
        self.chips_frame = tk.Frame(inner, bg=theme.BG)
        self.chips_frame.pack(fill="x", padx=theme.scale(28), pady=(0, theme.scale(8)))
        self._rebuild_chips()

        # 板块3：追踪与程序控制（最多 3 个按钮并排，适配 760px 最小窗口宽度）
        sec3 = section()
        RoundedButton(sec3, text="保存设置", width=112, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), command=self._save_settings,
                      fill=theme.ACCENT, fg="#FFFFFF",
                      hover=theme.ACCENT_HOVER).pack(side="left", padx=theme.scale(4))
        self.tracking_toggle_btn = RoundedButton(
            sec3, text="暂停追踪", width=112, height=32, radius=theme.CONTROL_RADIUS,
            font=theme.font(10), command=self._toggle_tracking,
            fill=theme.SECONDARY_BG, fg=theme.TEXT,
        )
        self.tracking_toggle_btn.pack(side="left", padx=theme.scale(4))
        RoundedButton(sec3, text="退出程序", width=112, height=32, radius=theme.CONTROL_RADIUS,
                      font=theme.font(10), command=self._quit_app,
                      fill=theme.SECONDARY_BG, fg=theme.TEXT).pack(side="left", padx=theme.scale(4))

        # 板块4：数据文件与维护
        sec5 = section()
        tk.Label(sec5, text="数据文件与维护", font=theme.font(10, True), fg=theme.TEXT,
                 bg=theme.BG).pack(anchor="w")
        self.db_path_label = tk.Label(sec5, text=str(self.db.db_path),
                                      font=theme.font(9), fg=theme.SUB, bg=theme.BG)
        self.db_path_label.pack(anchor="w", pady=(4, 8))
        wrap_to_area(self.db_path_label, sc)

        maint_btns = tk.Frame(sec5, bg=theme.BG)
        maint_btns.pack(anchor="w")
        RoundedButton(maint_btns, text="打开数据目录", width=116, height=32,
                      radius=theme.CONTROL_RADIUS, font=theme.font(10),
                      fill=theme.SECONDARY_BG, fg=theme.TEXT,
                      command=lambda: os.startfile(str(self.db.db_path.parent)),
                      bg=theme.BG).pack(side="left", padx=(0, theme.scale(6)))
        RoundedButton(maint_btns, text="备份数据库", width=112, height=32,
                      radius=theme.CONTROL_RADIUS, font=theme.font(10),
                      fill=theme.SECONDARY_BG, fg=theme.TEXT,
                      command=self._backup_db_ui,
                      bg=theme.BG).pack(side="left", padx=(0, theme.scale(6)))
        RoundedButton(maint_btns, text="清空全部历史", width=116, height=32,
                      radius=theme.CONTROL_RADIUS, font=theme.font(10),
                      fill=theme.SECONDARY_BG, fg="#C9342B",
                      command=self._purge_all_ui,
                      bg=theme.BG).pack(side="left")

        # 板块5：当前前台窗口预览
        sec6 = section()
        tk.Label(sec6, text="当前前台窗口（仅预览，不参与计时）", font=theme.font(10, True),
                 fg=theme.TEXT, bg=theme.BG).pack(anchor="w")
        self.settings_now_label = tk.Label(sec6, text="--", font=theme.font(10),
                                           fg=theme.SUB, bg=theme.BG, justify="left")
        self.settings_now_label.pack(anchor="w", pady=6)
        wrap_to_area(self.settings_now_label, sc)

    def _on_toggle_autostart(self):
        want = bool(self.var_autostart.get())
        try:
            self._set_autostart(want)
        except Exception as exc:  # noqa: BLE001
            self.var_autostart.set(not want)
            self._log_error("autostart", exc)
            messagebox.showerror("开机自启", f"更新开机自启设置失败：{exc}")

    def _save_settings(self):
        try:
            interval = float(self.var_interval.get())
            minsec = int(float(self.var_minsec.get()))
            idle_sec = max(0, int(float(self.var_idle.get())))
            retention = max(0, int(float(self.var_retention.get())))
        except ValueError:
            messagebox.showwarning("设置", "采样间隔 / 最短会话 / 空闲暂停 / 保留天数请输入数字")
            return

        updated = dict(self.cfg)
        updated["poll_interval_seconds"] = interval
        updated["min_session_seconds"] = minsec
        updated["idle_timeout_seconds"] = idle_sec
        updated["retention_days"] = retention
        updated["browser_site_tracking"] = bool(self.var_site.get())
        updated["strip_url_query"] = bool(self.var_strip_query.get())
        updated["store_full_url"] = bool(self.var_full_url.get())

        cfg_norm, _ = normalize_config(updated)
        self.cfg.update(cfg_norm)
        self.var_interval.set(str(self.cfg["poll_interval_seconds"]))
        self.var_minsec.set(str(int(self.cfg["min_session_seconds"])))
        self.var_idle.set(str(self.cfg["idle_timeout_seconds"]))
        self.var_retention.set(str(self.cfg["retention_days"]))

        self._persist_exclude()
        self.db.url_policy = UrlPolicy.from_config(self.cfg)
        if self.cfg["retention_days"] > 0:
            try:
                apply_retention(self.db, self.cfg["retention_days"])
            except Exception as exc:  # noqa: BLE001
                self._log_error("retention", exc)

        was_running = self._background_running()
        if was_running and hasattr(self, "_restart_background"):
            self._restart_background()
            messagebox.showinfo("设置", "设置已保存并自动重启后台采集，现已立即生效。")
        else:
            messagebox.showinfo("设置", "设置已保存。")

    def _add_exclude(self):
        raw = self.var_exclude.get().replace("，", ",")
        existing = {x.lower() for x in self.cfg.get("exclude_processes", [])}
        added = False
        for part in raw.split(","):
            name = part.strip().lower()
            if name and name not in existing:
                self.cfg.setdefault("exclude_processes", []).append(name)
                existing.add(name)
                added = True
        self.var_exclude.set("")
        if added:
            self._rebuild_chips()
            self._persist_exclude()
            if self._background_running() and hasattr(self, "_restart_background"):
                self._restart_background()

    def _remove_exclude(self, name):
        self.cfg["exclude_processes"] = [
            x for x in self.cfg.get("exclude_processes", []) if x != name
        ]
        self._rebuild_chips()
        self._persist_exclude()
        if self._background_running() and hasattr(self, "_restart_background"):
            self._restart_background()

    def _rebuild_chips(self):
        for w in self.chips_frame.winfo_children():
            w.destroy()
        excludes = self.cfg.get("exclude_processes", [])
        if not excludes:
            tk.Label(self.chips_frame, text="未排除任何进程", font=theme.font(9),
                     fg=theme.SUB, bg=theme.BG).pack(side="left")
            return
        try:
            chip_font = tkfont.Font(font=theme.font(9))
        except tk.TclError:
            chip_font = None
        for name in excludes:
            chip = tk.Canvas(self.chips_frame, bg=theme.BG, highlightthickness=0, bd=0,
                             height=theme.scale(26))
            label_text = name + "  ×"

            def draw(c=chip, t=label_text):
                c.delete("all")
                w = c.winfo_width()
                h = c.winfo_height()
                if w <= 2:
                    return
                pts = rounded_polygon(1, 1, w - 1, h - 1, theme.scale(6))
                c.create_polygon(pts, smooth=True, fill=theme.ACCENT_LIGHTER, outline="")
                c.create_text(w / 2, h / 2, text=t, font=theme.font(9), fill=theme.ACCENT)

            chip.bind("<Configure>", lambda _e, fn=draw: fn())
            chip.bind("<Map>", lambda _e, fn=draw: fn())
            chip.bind("<Button-1>", lambda _e, nn=name: self._remove_exclude(nn))
            chip.pack(side="left", padx=(0, theme.scale(6)), pady=2)
            if chip_font is not None:
                est_w = chip_font.measure(label_text) + theme.scale(20)
            else:
                est_w = theme.scale(10) * len(name) + theme.scale(28)
            chip.configure(width=max(theme.scale(70), int(est_w)))

    def _persist_exclude(self):
        cfg_path = project_root() / "config.json"
        tmp_path = cfg_path.with_suffix(".json.tmp")
        payload = json.dumps(self.cfg, ensure_ascii=False, indent=2)
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(cfg_path)

    def _clear_tracking_lock(self):
        """优雅停止正在运行的后台采集进程并清理残留锁文件。"""
        if hasattr(self, "_stop_background_process"):
            self._stop_background_process()
        lock = TrackingLock(self.cfg)
        try:
            if lock.holder_alive():
                lock.request_stop(timeout=2.5)
            else:
                lock.force_unlock()
        except LockError as exc:
            messagebox.showerror("停止追踪", f"停止后台采集失败：{exc}")
            return
        messagebox.showinfo("停止追踪", "后台采集已安全停止（当前会话已保存）。")

    def _toggle_tracking(self):
        if self._background_running():
            self._clear_tracking_lock()
        else:
            self._spawn_background()
        try:
            self.refresh()
        except Exception:  # noqa: BLE001
            pass

    def _backup_db_ui(self):
        try:
            target = self.db.db_path.with_name(
                f"{self.db.db_path.name}.bak-{datetime.now():%Y%m%d_%H%M%S}"
            )
            with sqlite3.connect(str(target)) as dest:
                with self.db._lock:
                    self.db.conn.backup(dest)
            size_mb = target.stat().st_size / 1024 / 1024
            messagebox.showinfo("备份数据库", f"备份完成（{size_mb:.2f} MB）：\n{target}")
        except Exception as exc:  # noqa: BLE001
            self._log_error("backup_db", exc)
            messagebox.showerror("备份失败", f"备份数据库失败：{exc}")

    def _purge_all_ui(self):
        if not messagebox.askyesno(
            "确认清空历史",
            "确定要永久清空全部屏幕使用时间历史记录吗？\n建议清空前先点击「备份数据库」。",
        ):
            return
        try:
            removed = self.db.purge_all_sessions()
            self.db.vacuum()
            self._rows_sig.clear()
            self._records_sig = None
            self._records_dirty = True
            messagebox.showinfo("清空完成", f"已清空 {removed} 条历史记录并压缩数据库空间。")
        except Exception as exc:  # noqa: BLE001
            self._log_error("purge_all", exc)
            messagebox.showerror("清空失败", f"清空历史记录失败：{exc}")

    def _refresh_settings_live(self, now: datetime):
        if hasattr(self, "tracking_toggle_btn"):
            running = self._background_running()
            self.tracking_toggle_btn.set_text("暂停追踪" if running else "恢复追踪")

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
