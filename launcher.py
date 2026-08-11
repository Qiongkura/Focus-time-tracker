"""打包入口：双击 exe 直接打开 GUI（等价于 main.py dashboard，自动拉起后台采集）。

带 start 参数时作为纯后台采集进程运行（由 GUI 内部自动拉起），两者通过
tracking.lock 互斥，保证同时只有一个采集进程写库。
"""
from __future__ import annotations

import sys

from tracker.config import load_config, project_root
from tracker.db import UsageDB


def _setup_paths():
    root = project_root()
    cfg = load_config(root / "config.json")
    data_dir = root / cfg["data_dir"]
    report_dir = root / cfg["report_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return cfg, data_dir / "usage.db", report_dir


def main():
    cfg, db_path, report_dir = _setup_paths()

    if len(sys.argv) > 1 and sys.argv[1] == "start":
        # 纯后台采集（exe 被 GUI 内部拉起的子进程模式）
        from main import _TrackingLock
        from tracker.monitor import run_tracking

        lock = _TrackingLock(cfg)
        if not lock.acquire():
            print("已有采集会话在运行，本次启动已取消。")
            return
        try:
            with UsageDB(db_path) as db:
                run_tracking(db, cfg)
        finally:
            lock.release()
        return

    # 默认：打开可视化界面（界面会自动拉起自身带 start 的后台采集子进程）
    import matplotlib
    matplotlib.use("TkAgg")
    from tracker.app import run_app

    with UsageDB(db_path) as db:
        run_app(db, cfg, report_dir)


if __name__ == "__main__":
    main()
