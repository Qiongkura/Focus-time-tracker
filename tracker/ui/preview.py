"""前台窗口预览采样线程。

从 ``tracker/app.py`` 拆出。这个 worker 把 ``get_foreground_info()`` 放到
独立线程，主线程只读最近一次结果，避免浏览器历史库被独占时拖住界面。
"""
from __future__ import annotations

import threading

from ..monitor import get_foreground_info


class ForegroundPreviewWorker:
    """后台采样「当前前台窗口」，供设置页预览使用。

    为什么需要它：``get_foreground_info()`` 内部会走到浏览器历史库查询——
    枚举 Chrome/Edge/Firefox 的配置目录、打开 History / places.sqlite，
    失败时还要把数据库整个复制到临时文件再查。即使已经有 3 秒查询节流和
    0.2 秒 SQLite 超时，遇到浏览器独占数据库或磁盘繁忙时，单次调用仍可能
    阻塞几百毫秒。这个调用原本发生在 Tk 主线程的刷新循环里（每 2 秒一次），
    于是设置页甚至整个界面会跟着顿一下。

    这里把采样整体挪到独立线程，主线程只读最近一次结果，不再碰 Win32
    与浏览器历史库。
    """

    def __init__(self, interval: float = 2.0):
        self._interval = max(0.5, float(interval))
        self._stop = threading.Event()
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

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[bool, dict | None]:
        """返回 ``(是否已产出过结果, 最近一次前台窗口信息)``。

        主线程只调用这个只读方法，不做任何 Win32 / 数据库操作。
        """
        with self._lock:
            return self._ready, self._latest

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                info = get_foreground_info()
            except Exception:  # noqa: BLE001 - 预览失败绝不能影响界面
                info = None
            with self._lock:
                self._latest = info
                self._ready = True
            if self._stop.wait(self._interval):
                break
