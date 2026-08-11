"""屏幕聚焦进程使用时长统计与可视化（Windows）。

用法:
  python main.py start              开始记录前台窗口使用时长（Ctrl+C 停止）
  python main.py dashboard          打开实时可视化面板
  python main.py report             生成今日报告图片
  python main.py report --days 7    生成近 7 天趋势图
  python main.py stats              在控制台查看今日统计
  python main.py now                查看当前前台窗口信息（诊断用）
  python main.py demo               生成 7 天示例数据，便于预览图表
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

from tracker.config import load_config, project_root
from tracker.db import UsageDB
from tracker.monitor import get_foreground_info, run_tracking
from tracker.utils import fmt_hms


def _setup_paths():
    root = project_root()
    cfg = load_config(root / "config.json")
    data_dir = root / cfg["data_dir"]
    report_dir = root / cfg["report_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return root, cfg, data_dir / "usage.db", report_dir


class _TrackingLock:
    """跨进程采集互斥锁（data/tracking.lock + PID 存活检查）。

    start 与 dashboard 共用同一把锁，保证同一时间只有一个 run_tracking
    采集线程在写入数据库，避免时长重复统计。
    """

    def __init__(self, cfg: dict):
        data_dir = project_root() / cfg.get("data_dir", "data")
        self.path = data_dir / "tracking.lock"

    @staticmethod
    def _process_alive_and_age(pid: int):
        """返回 (是否存活, 进程创建时间[微秒,1601纪元])；异常时保守返回 (True, 0)。"""
        try:
            import ctypes
            from ctypes import wintypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False, 0
            try:
                ct = wintypes.FILETIME()
                et = wintypes.FILETIME()
                kt = wintypes.FILETIME()
                ut = wintypes.FILETIME()
                if ctypes.windll.kernel32.GetProcessTimes(
                        handle, ctypes.byref(ct), ctypes.byref(et),
                        ctypes.byref(kt), ctypes.byref(ut)):
                    created = (ct.dwHighDateTime << 32) | ct.dwLowDateTime
                    return True, created
                return True, 0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return True, 0

    def acquire(self) -> bool:
        try:
            if self.path.exists():
                content = self.path.read_text(encoding="utf-8").strip()
                pid_s, _, created_s = content.partition("|")
                if pid_s.isdigit():
                    alive, created = self._process_alive_and_age(int(pid_s))
                    same_created = created_s.isdigit() and created == int(created_s)
                    # 新格式校验进程创建时间，防止 PID 被复用导致误判
                    if alive and (created_s == "" or same_created):
                        return False
                self.path.unlink(missing_ok=True)
            _alive, created = self._process_alive_and_age(os.getpid())
            self.path.write_text(f"{os.getpid()}|{created}", encoding="utf-8")
            return True
        except Exception:
            return True

    def release(self) -> None:
        try:
            if self.path.exists():
                content = self.path.read_text(encoding="utf-8").strip()
                pid_s, _, created_s = content.partition("|")
                if pid_s == str(os.getpid()):
                    _alive, created = self._process_alive_and_age(os.getpid())
                    if created_s == "" or (created_s.isdigit() and created == int(created_s)):
                        self.path.unlink(missing_ok=True)
        except Exception:
            pass


def cmd_start(args):
    """纯后台采集：不启动 GUI，与 dashboard 互斥（同一把锁）。"""
    _, cfg, db_path, _ = _setup_paths()
    lock = _TrackingLock(cfg)
    if not lock.acquire():
        print("【警告】已有采集会话在运行（可能是 dashboard 或其他 start）。为避免重复统计，本次启动已取消。")
        return
    try:
        with UsageDB(db_path) as db:
            run_tracking(db, cfg)
    finally:
        lock.release()


def cmd_now(args):
    info = get_foreground_info()
    if not info:
        print("未获取到前台窗口")
        return
    print(f"进程: {info['process']}")
    print(f"窗口: {info['title'] or '（无标题）'}")
    print(f"PID : {info['pid']}")
    print(f"路径: {info['exe_path'] or '（无）'}")


def cmd_stats(args):
    _, _, db_path, _ = _setup_paths()
    with UsageDB(db_path) as db:
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        data = db.summary_between(start, now)
        total = sum(d["seconds"] for d in data)
        print(f"今日屏幕使用统计（{start:%Y-%m-%d}）")
        print("-" * 48)
        if not data:
            print("暂无记录。请先运行: python main.py start")
        else:
            for i, d in enumerate(data, 1):
                pct = d["seconds"] / total * 100
                print(f"{i:>2}. {d['process']:<16} {fmt_hms(d['seconds'])}  {pct:5.1f}%")
        print("-" * 48)
        print(f"合计: {fmt_hms(total)}")


def cmd_report(args):
    import matplotlib
    matplotlib.use("Agg")
    from tracker.report import generate_today_chart, generate_week_chart

    _, _, db_path, report_dir = _setup_paths()
    with UsageDB(db_path) as db:
        if args.days <= 1:
            path = report_dir / f"今日使用_{datetime.now():%Y%m%d}.png"
            generate_today_chart(db, path)
        else:
            path = report_dir / f"近{args.days}天使用_{datetime.now():%Y%m%d}.png"
            generate_week_chart(db, path, days=args.days)
    print(f"报告已生成：{path}")


def cmd_dashboard(args):
    # dashboard 内部自带窗口采集，与 start 互斥（tracking.lock 保证同一时间只有一个采集线程）
    print("【注意】dashboard 内部自带窗口采集，请勿再在别的终端执行 python main.py start，否则会重复统计时长！")
    import matplotlib
    matplotlib.use("TkAgg")
    from tracker.app import run_app

    _, cfg, db_path, report_dir = _setup_paths()
    with UsageDB(db_path) as db:
        run_app(db, cfg, report_dir)


def cmd_unlock(args):
    """强制清除采集锁文件（进程已确认退出、但锁残留时使用）。"""
    _, cfg, _, _ = _setup_paths()
    lock = _TrackingLock(cfg)
    if lock.path.exists():
        lock.path.unlink(missing_ok=True)
        print("已清除采集锁文件。若确实还有采集进程在运行，请先关闭它，否则会重复统计。")
    else:
        print("当前没有锁文件，无需解锁。")


def cmd_game(args):
    """游戏识别规则诊断：查看生效规则 / 按规则判断某个进程是否为游戏。"""
    from tracker.games import GAME_RULES_FILENAME, is_game, load_rules

    if args.action == "rules":
        rules = load_rules()
        print("当前生效的游戏识别规则（内置默认 + game_rules.json 覆盖）：")
        for key, values in rules.items():
            text = "、".join(values) if values else "（空）"
            print(f"  {key}: {text}")
        rules_path = project_root() / GAME_RULES_FILENAME
        print(f"\n规则文件：{rules_path}（存在：{rules_path.exists()}）")
    elif args.action == "check":
        result = is_game(args.path, args.process, args.title)
        print("是游戏" if result else "不是游戏")


def cmd_demo(args):
    from tracker.demo import seed_demo_data

    _, _, db_path, _ = _setup_paths()
    with UsageDB(db_path) as db:
        seed_demo_data(db, days=7)


def main():
    parser = argparse.ArgumentParser(description="屏幕聚焦进程使用时长统计与可视化（仅 Windows）")
    sub = parser.add_subparsers(dest="command", required=True)

    # start：纯后台采集，不启动 GUI；dashboard：GUI + 内部采集。二者互斥。
    sub.add_parser("start", help="纯后台采集前台窗口使用时长（Ctrl+C 停止，与 dashboard 互斥）")
    sub.add_parser("dashboard", help="打开可视化界面并自动开始追踪（与 start 互斥）")
    sub.add_parser("stats", help="在控制台查看今日统计")
    sub.add_parser("unlock", help="清除残留的采集锁文件（卡在“已有采集会话”时使用）")
    p_report = sub.add_parser("report", help="生成报告图片")
    p_report.add_argument("--days", type=int, default=1, help="统计天数，1=今日，7=近7天（默认 1）")
    sub.add_parser("now", help="查看当前前台窗口信息（诊断用）")
    sub.add_parser("demo", help="生成 7 天示例数据，便于预览图表")
    p_game = sub.add_parser("game", help="游戏识别规则诊断")
    g_sub = p_game.add_subparsers(dest="action", required=True)
    g_sub.add_parser("rules", help="打印当前生效的游戏识别规则")
    p_g_check = g_sub.add_parser("check", help="按规则判断某个进程/路径是否为游戏")
    p_g_check.add_argument("--process", default="", help="进程名，如 VALORANT.exe")
    p_g_check.add_argument("--path", default="", help="exe 完整路径，如 C:\\Riot Games\\VALORANT\\live\\VALORANT.exe")
    p_g_check.add_argument("--title", default="", help="窗口标题（可选）")

    args = parser.parse_args()
    handlers = {
        "start": cmd_start,
        "dashboard": cmd_dashboard,
        "stats": cmd_stats,
        "report": cmd_report,
        "now": cmd_now,
        "demo": cmd_demo,
        "unlock": cmd_unlock,
        "game": cmd_game,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
