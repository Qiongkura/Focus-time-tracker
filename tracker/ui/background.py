"""后台采集子进程与系统托盘的托管。

从 ``tracker/app.py`` 拆出。这些方法只依赖宿主的 ``cfg`` / ``db`` / ``root``
以及 ``_log_error()`` / ``_quit_app()``，因此整体搬迁不影响调用点。
"""
from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox

from .. import tray
from ..config import project_root
from ..lock import LockError, TrackingLock

# CREATE_NO_WINDOW：pythonw 启动时不要再弹出控制台窗口
_CREATE_NO_WINDOW = 0x08000000


class BackgroundMixin:
    """后台采集进程的拉起/停止、托盘集成与启动期数据自检。"""

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
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            # pythonw 下 stdout 不可见，必须落日志才排查得到
            self._log_error("spawn_background", exc)
            self._bg_proc = None

    def _stop_background_process(self):
        """优先通过停止信号让后台采集保存最后一段会话并正常释放锁，超时后再强杀兜底。"""
        try:
            TrackingLock(self.cfg).request_stop(timeout=2.5)
        except Exception:  # noqa: BLE001
            pass

        if self._bg_proc is None:
            return
        if self._bg_proc.poll() is None:
            try:
                self._bg_proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                try:
                    self._bg_proc.terminate()
                    self._bg_proc.wait(timeout=2.0)
                except Exception:  # noqa: BLE001
                    try:
                        self._bg_proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        self._bg_proc = None

    def _restart_background(self):
        """重启后台采集子进程（设置保存或排除列表变更后使新配置立即生效）。"""
        self._stop_background_process()
        self._spawn_background()

    # ---------- 系统托盘 ----------
    def _setup_tray(self):
        try:
            icon_dir = project_root() / self.cfg.get("data_dir", "data")
            icon_dir.mkdir(parents=True, exist_ok=True)
            self._tray_icon_path = icon_dir / "tray_icon.ico"
            if not tray.create_icon(self._tray_icon_path):
                return
            # 通过 after(0, ...) 异步切回 Tk 主循环执行，避免在托盘消息窗口自身的
            # _wnd_proc 调用栈内重入销毁窗口。
            self._tray_enabled = tray.enable_tray(
                str(self._tray_icon_path),
                on_open=lambda: self.root.after(0, self._restore_from_tray),
                on_quit=lambda: self.root.after(0, self._quit_app),
                tooltip="屏幕使用时间 · 使用时长记录",
            )
        except Exception as exc:  # noqa: BLE001
            # pythonw 下 stdout 不可见，必须落日志才排查得到
            self._log_error("tray_init", exc)
            self._tray_enabled = False

    def _restore_from_tray(self):
        if getattr(self, "_closing", False):
            return
        self._hidden_to_tray = False
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.lift()
            self.root.focus_force()
            self.root.title("屏幕使用时间")
            if getattr(self, "current_page", "") == "settings" and hasattr(self, "_preview"):
                self._preview.set_active(True)
            if getattr(self, "_refresh_timer", None) is None:
                self._refresh_loop()
            elif getattr(self, "current_page", "") == "stats":
                self._schedule_stats_draw()
        except tk.TclError:
            pass

    # ---------- 数据校验（只读） ----------
    def _check_overlap_sessions(self):
        """启动时只在最近 7 天内查重叠记录。"""
        try:
            since = datetime.now() - timedelta(days=7)
            pairs = self.db.find_overlapping_sessions(since=since, limit=1)
        except Exception as exc:  # noqa: BLE001
            self._log_error("overlap_check", exc)
            return
        if pairs:
            # 留 id：用户可能在弹窗前的这 800ms 里就把窗口关了
            self._dialog_timer = self.root.after(800, lambda: messagebox.showwarning(
                "检测到重叠记录",
                "最近 7 天存在时间重叠的会话，可能是之前同时运行过多个采集进程造成的重复统计。\n"
                "时长采集请只使用一个后台进程。\n\n"
                "如需完整检查，请在项目目录运行：python main.py doctor"))

    def _background_running(self) -> bool:
        """后台采集是否在跑——直接复用 TrackingLock.holder_alive()。"""
        try:
            return TrackingLock(self.cfg).holder_alive()
        except LockError:
            return False
