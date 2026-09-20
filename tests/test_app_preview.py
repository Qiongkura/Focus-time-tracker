"""前台窗口预览 worker：采样必须在后台线程完成，且不能因异常死掉。

worker 已从 `tracker/app.py` 搬到 `tracker/ui/preview.py`，因此打桩要指向
`tracker.ui.preview` 模块里的 `get_foreground_info`——worker 调用的是它自己
模块命名空间中的那个名字。`ForegroundPreviewWorker` 仍从 `tracker.app`
导入，顺带验证重新导出没有断。
"""
from __future__ import annotations

import time

from tracker.app import ForegroundPreviewWorker
from tracker.ui import preview as preview_module


def _wait_ready(worker: ForegroundPreviewWorker, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, info = worker.latest()
        if ready:
            return info
        time.sleep(0.02)
    raise AssertionError("worker 在超时前没有产出采样结果")


def test_latest_is_empty_before_start():
    worker = ForegroundPreviewWorker()
    assert worker.latest() == (False, None)


def test_worker_publishes_sample(monkeypatch):
    monkeypatch.setattr(preview_module, "get_foreground_info",
                        lambda: {"process": "code.exe", "title": "t", "category": "应用"})
    worker = ForegroundPreviewWorker(interval=0.05)
    worker.start()
    try:
        assert _wait_ready(worker)["process"] == "code.exe"
    finally:
        worker.stop()


def test_worker_survives_sampling_errors(monkeypatch):
    """采样抛异常时降级为 None，线程不能死掉（否则预览永久停在旧值）。"""
    def boom():
        raise RuntimeError("浏览器历史库被占用")

    monkeypatch.setattr(preview_module, "get_foreground_info", boom)
    worker = ForegroundPreviewWorker(interval=0.05)
    worker.start()
    try:
        assert _wait_ready(worker) is None
        # 线程仍存活，且持续产出（ready 保持 True）
        time.sleep(0.15)
        ready, info = worker.latest()
        assert ready is True
        assert info is None
    finally:
        worker.stop()


def test_stop_does_not_wait_for_full_interval(monkeypatch):
    monkeypatch.setattr(preview_module, "get_foreground_info", lambda: None)
    worker = ForegroundPreviewWorker(interval=30)
    worker.start()
    time.sleep(0.2)
    worker.stop()
    worker._thread.join(timeout=5)
    assert not worker._thread.is_alive(), "stop() 后线程没有及时退出"


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(preview_module, "get_foreground_info", lambda: None)
    worker = ForegroundPreviewWorker(interval=0.05)
    worker.start()
    first = worker._thread
    worker.start()
    try:
        assert worker._thread is first
    finally:
        worker.stop()
