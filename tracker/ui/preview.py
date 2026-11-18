"""前台窗口预览采样线程。

从 ``tracker/app.py`` 拆出。这个 worker 把 ``get_foreground_info()`` 放到
独立线程，主线程只读最近一次结果，避免浏览器历史库被独占时拖住界面。
"""
from __future__ import annotations

import threading

from ..monitor import get_foreground_info


class ForegroundPreviewWorker:
    """后台采样「当前前台窗口」，供设置页预览使用。

    支持按需启停采样（``set_active``）：仅当用户停留在「设置」页且窗口可见时才激活，
    平时与隐藏到托盘时完全休眠（零 CPU / 零历史库 I/O）。直接实例化时默认处于激活状态，
    保持独立调用与原有单元测试行为不变。
    """

    def __init__(self, interval: float = 2.0):
        self._interval = max(0.5, float(interval))
        self._stop = threading.Event()
        self._active = threading.Event()
        self._active.set()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._ready = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="foreground-preview", daemon=True)
        self._thread.start()

    def set_active(self, active: bool) -> None:
        """切换采样激活状态：未激活时后台线程阻塞等待，不消耗资源。"""
        if active:
            self._active.set()
        else:
            self._active.clear()

    def stop(self) -> None:
        self._stop.set()
        self._active.set()

    def latest(self) -> tuple[bool, dict | None]:
        """返回 ``(是否已产出过结果, 最近一次前台窗口信息)``。

        主线程只调用这个只读方法，不做任何 Win32 / 数据库操作。
        """
        with self._lock:
            return self._ready, self._latest

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._active.wait(timeout=0.5):
                continue
            if self._stop.is_set():
                break
            try:
                info = get_foreground_info()
            except Exception:  # noqa: BLE001 - 预览失败绝不能影响界面
                info = None
            with self._lock:
                self._latest = info
                self._ready = True
            if self._stop.wait(self._interval):
                break
