"""窗口外壳：DPI、样式、侧边栏、页面容器、日志与生命周期。

从 ``tracker/app.py`` 拆出。``rotate_log`` 也搬到这里（原本只被
``_log_error`` 使用），并由 ``tracker.app`` 重新导出，保证
``from tracker.app import rotate_log`` 仍然可用。
"""
from __future__ import annotations

import ctypes
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from .. import theme, tray
from ..config import project_root
from ..widgets import (
    NavButton, get_brand_logo, rounded_polygon, stop_all_scroll_area_timers,
)

NAV_PAGES = [
    ("home", "🏠", "首页"),
    ("stats", "📊", "统计"),
    ("records", "📋", "详细记录"),
    ("categories", "🗂️", "分类"),
]


def rotate_log(path: Path, max_bytes: int = 1_000_000, keep: int = 3) -> None:
    """日志超过 ``max_bytes`` 时轮转：``x.log`` → ``x.log.1`` → ``x.log.2`` …

    长期运行的桌面程序如果只追加不轮转，日志会一直膨胀——用户机器上的
    ``data/ui_errors.log`` 实测已经涨到 500KB 以上。这里保留最近 ``keep`` 份。
    """
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    try:
        path.with_name(f"{path.name}.{keep}").unlink(missing_ok=True)
    except OSError:
        pass
    for index in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        try:
            if src.exists():
                src.replace(dst)
        except OSError:
            pass
    try:
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        pass



class ShellMixin:
    """主窗口的骨架与生命周期。"""

    # ---------- DPI ----------
    def _setup_dpi(self) -> float:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if dpi <= 0:
                dpi = 96
        except Exception:
            dpi = 96
        factor = dpi / 96.0
        theme.set_scale(factor)
        try:
            self.root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        return factor

    # ---------- 基础 ----------
    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        padding = (8, 5)
        style.configure("TCombobox", fieldbackground="#FFFFFF", background="#FFFFFF",
                        foreground=theme.TEXT, font=theme.font(10),
                        arrowcolor=theme.ACCENT, bordercolor="#D9D9DE", padding=padding)
        # 修复：下拉框点开不选后文字变白不可见——显式固定 readonly 状态配色
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#FFFFFF")],
                  foreground=[("readonly", theme.TEXT)],
                  selectbackground=[("readonly", theme.ACCENT)],
                  selectforeground=[("readonly", "#FFFFFF")])
        self.root.option_add("*TCombobox*Listbox.background", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.foreground", theme.TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", theme.ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
        style.configure("TSpinbox", fieldbackground="#FFFFFF", foreground=theme.TEXT,
                        font=theme.font(10), arrowsize=12, bordercolor="#D9D9DE",
                        padding=padding)
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=theme.TEXT,
                        font=theme.font(10), bordercolor="#D9D9DE", padding=padding)
        style.configure("TCheckbutton", background=theme.BG, foreground=theme.TEXT,
                        font=theme.font(10), padding=(4, 5))

    def _build_sidebar(self):
        k = theme.scale(1.0)
        self.sidebar = tk.Frame(self.root, bg=theme.SIDEBAR_BG,
                                width=theme.scale(theme.SIDEBAR_WIDTH))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        brand.pack(fill="x", pady=(theme.scale(24), theme.scale(20)))
        self._make_brand(brand)

        nav_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        nav_frame.pack(fill="x")
        self.nav_buttons = {}
        for key, icon, text in NAV_PAGES:
            btn = NavButton(nav_frame, text=text, icon=icon,
                            command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=theme.scale(10), pady=theme.scale(3))
            self.nav_buttons[key] = btn

        settings_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        settings_frame.pack(side="bottom", fill="x")
        self.nav_buttons["settings"] = NavButton(
            settings_frame, text="设置", icon="⚙️",
            command=lambda: self.show_page("settings"))
        self.nav_buttons["settings"].pack(fill="x", padx=theme.scale(10), pady=theme.scale(3))

        status_frame = tk.Frame(self.sidebar, bg=theme.SIDEBAR_BG)
        status_frame.pack(side="bottom", fill="x", pady=(0, theme.scale(18)))
        self.status_dot = tk.Label(status_frame, text="●", font=("Segoe UI", 10),
                                   fg=theme.SUB, bg=theme.SIDEBAR_BG)
        self.status_dot.pack(side="left", padx=(theme.scale(14), 4))
        self.status_text = tk.Label(status_frame, text="后台未运行", font=theme.font(9),
                                    fg=theme.SUB, bg=theme.SIDEBAR_BG)
        self.status_text.pack(side="left", padx=(0, theme.scale(6)))

    def _make_brand(self, parent):
        canvas = tk.Canvas(parent, width=theme.scale(136), height=theme.scale(48),
                           bg=theme.SIDEBAR_BG, highlightthickness=0, bd=0)
        canvas.bind("<Configure>", lambda _e: self._draw_brand(canvas))
        canvas.bind("<Map>", lambda _e: self._draw_brand(canvas))
        canvas.pack(anchor="w", padx=theme.scale(12))

    def _draw_brand(self, canvas):
        canvas.delete("all")
        k = theme.scale(1.0)
        x0, y0 = 0, 4 * k
        side = 38 * k
        logo = get_brand_logo(int(side))
        if logo is not None:
            self._brand_photo = logo  # 持有引用，防止被回收
            canvas.create_image(x0, y0, image=logo, anchor="nw")
        else:
            # 资源缺失时回退到旧版手绘图标
            base = rounded_polygon(x0, y0, x0 + side, y0 + side, 11 * k)
            canvas.create_polygon(base, smooth=True, fill=theme.ACCENT, outline="")
            gloss = rounded_polygon(x0 + 1, y0 + 1, x0 + side - 1, y0 + side * 0.42, 9 * k)
            canvas.create_polygon(gloss, smooth=True, fill="#3395FF", outline="")
            cx, cy, r = x0 + side / 2, y0 + side / 2, 13 * k
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               outline="#FFFFFF", width=2.2 * k)
            canvas.create_line(cx, cy, cx, cy - 6.5 * k, fill="#FFFFFF",
                               width=2.2 * k, capstyle="round")
            canvas.create_line(cx, cy, cx + 5 * k, cy + 4 * k, fill="#FFFFFF",
                               width=2.2 * k, capstyle="round")
        tx = x0 + side + theme.scale(10)
        canvas.create_text(tx, y0 + theme.scale(7), text="屏幕时间", anchor="w",
                           font=theme.font(15, True), fill=theme.TEXT_TITLE)
        canvas.create_text(tx, y0 + side - theme.scale(9), text="使用时长记录", anchor="w",
                           font=theme.font(9), fill=theme.SUB)

    def _build_pages(self):
        self.main = tk.Frame(self.root, bg=theme.BG)
        self.main.pack(side="left", fill="both", expand=True)
        self.pages = {key: tk.Frame(self.main, bg=theme.BG) for key, _, _ in NAV_PAGES}
        self.pages["settings"] = tk.Frame(self.main, bg=theme.BG)
        self._build_home()
        self._build_stats()
        self._build_records()
        self._build_categories()
        self._build_settings()

    def show_page(self, key: str):
        self.current_page = key
        for k, page in self.pages.items():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for nk, btn in self.nav_buttons.items():
            btn.set_selected(nk == key)
        if key == "records":
            self._records_dirty = True
            self._refresh_records()
        elif key == "stats":
            self._schedule_stats_draw()


    def _log_error(self, where: str, exc: Exception):
        """把界面异常写入 data/ui_errors.log，便于排查 pythonw 下的静默错误。

        文件超过 1MB 时自动轮转（保留最近 3 份），避免长期运行无限增长。
        """
        try:
            import traceback as _tb
            log_dir = project_root() / self.cfg.get("data_dir", "data")
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / "ui_errors.log"
            rotate_log(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {where}: {exc}\n")
                f.write(_tb.format_exc() + "\n")
        except Exception:  # noqa: BLE001
            # 日志本身失败时静默，绝不能反过来影响界面
            pass


    # ---------- 生命周期 ----------
    def _on_close(self):
        """点右上角 × ：隐藏到系统托盘（后台采集不受影响）。"""
        if self._tray_enabled:
            self._hide_to_tray()
        else:
            self.shutdown()

    def _hide_to_tray(self):
        self.root.withdraw()
        self.root.title("屏幕使用时间 · 后台记录中")
        if not self._minimize_hint_shown:
            self._minimize_hint_shown = True
            self._dialog_timer = self.root.after(600, lambda: messagebox.showinfo(
                "后台记录中",
                "窗口已隐藏到系统托盘（任务栏右下角），后台采集不受影响。\n"
                "双击托盘图标恢复窗口；右键托盘图标可退出。"))

    def _on_map(self):
        try:
            if self.root.state() == "normal" and self._minimized:
                self._minimized = False
                self.root.title("屏幕使用时间")
        except tk.TclError:
            pass

    def _quit_app(self):
        self.shutdown()

    def shutdown(self):
        self._closing = True
        self._cancel_timers()
        self._preview.stop()
        self._stop_background_process()
        if self._tray_enabled:
            try:
                tray.disable_tray()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _cancel_timers(self):
        """取消所有排队中的 after，然后才能安全 destroy。

        ``root.destroy()`` 会把控件注册的 Tcl 命令一起删掉，但**不会**取消
        已经排队的定时器。到点后 Tk 找不到回调命令，就往 stderr 刷
        ``invalid command name "..." (after script)``；如果那时正好有个
        refresh 在跑，还会顺着 `_log_error` 写进 data/ui_errors.log。
        """
        for attr in ("_refresh_timer", "_boot_refresh_timer", "_boot_warm_timer",
                     "_hourly_timer", "_stats_redraw_timer", "_resize_timer",
                     "_content_timer", "_banner_timer", "_dialog_timer"):
            tid = getattr(self, attr, None)
            if tid:
                try:
                    self.root.after_cancel(tid)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, attr, None)
        try:
            stop_all_scroll_area_timers()
        except Exception:  # noqa: BLE001
            pass

    def run(self):
        self.root.mainloop()
