"""前台窗口预览 worker：采样必须在后台线程完成，且不能因异常死掉。"""
from __future__ import annotations

import time

from tracker import app as app_module
from tracker.app import ForegroundPreviewWorker


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
    monkeypatch.setattr(app_module, "get_foreground_info",
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

    monkeypatch.setattr(app_module, "get_foreground_info", boom)
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
    monkeypatch.setattr(app_module, "get_foreground_info", lambda: None)
    worker = ForegroundPreviewWorker(interval=30)
    worker.start()
    time.sleep(0.2)
    worker.stop()
    worker._thread.join(timeout=5)
    assert not worker._thread.is_alive(), "stop() 后线程没有及时退出"


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(app_module, "get_foreground_info", lambda: None)
    worker = ForegroundPreviewWorker(interval=0.05)
    worker.start()
    first = worker._thread
    worker.start()
    try:
        assert worker._thread is first
    finally:
        worker.stop()
