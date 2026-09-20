"""打包入口：双击 exe 直接打开 GUI（等价于 main.py dashboard，自动拉起后台采集）。

带 start 参数时作为纯后台采集进程运行（由 GUI 内部自动拉起），两者通过
tracking.lock 互斥，保证同时只有一个采集进程写库。
"""
from __future__ import annotations

import sys

from tracker.config import load_config, project_root
from tracker.db import UsageDB
from tracker.privacy import UrlPolicy, apply_retention


def _setup_paths():
    root = project_root()
    cfg = load_config(root / "config.json")
    data_dir = root / cfg["data_dir"]
    report_dir = root / cfg["report_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return cfg, data_dir / "usage.db", report_dir


def _open_db(db_path, cfg) -> UsageDB:
    """按配置打开数据库：URL 落库策略来自 config（见 tracker/privacy.py）。"""
    return UsageDB(db_path, url_policy=UrlPolicy.from_config(cfg))


def main():
    cfg, db_path, report_dir = _setup_paths()

    if len(sys.argv) > 1 and sys.argv[1] == "start":
        # 纯后台采集（exe 被 GUI 内部拉起的子进程模式）
        from tracker.lock import LockError, TrackingLock
        from tracker.monitor import run_tracking

        lock = TrackingLock(cfg)
        try:
            acquired = lock.acquire()
        except LockError as exc:
            # fail-closed：锁不可用时宁可拒绝启动，也不要冒重复统计的风险
            print(f"采集锁不可用，本次启动已取消：{exc}")
            return
        if not acquired:
            print("已有采集会话在运行，本次启动已取消。")
            return
        try:
            with _open_db(db_path, cfg) as db:
                removed = apply_retention(db, cfg.get("retention_days", 0))
                if removed:
                    print(f"已按保留策略（{cfg.get('retention_days')} 天）"
                          f"清理 {removed} 条历史记录")
                run_tracking(db, cfg)
        finally:
            try:
                lock.release()
            except LockError as exc:
                print(f"释放采集锁失败：{exc}")
        return

    # 默认：打开可视化界面（界面会自动拉起自身带 start 的后台采集子进程）
    import matplotlib
    matplotlib.use("TkAgg")
    from tracker.app import run_app

    with _open_db(db_path, cfg) as db:
        run_app(db, cfg, report_dir)


if __name__ == "__main__":
    main()
