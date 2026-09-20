"""屏幕聚焦进程使用时长统计与可视化（Windows）。

用法:
  python main.py start              开始记录前台窗口使用时长（Ctrl+C 停止）
  python main.py dashboard          打开实时可视化面板
  python main.py report             生成今日报告图片
  python main.py report --days 7    生成近 7 天趋势图
  python main.py stats              在控制台查看今日统计
  python main.py now                查看当前前台窗口信息（诊断用）
  python main.py doctor             自检：残留锁 / 重叠记录 / 数据库体积
  python main.py demo               生成 7 天示例数据，便于预览图表
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from tracker.config import load_config, project_root
from tracker.db import UsageDB
from tracker.lock import LockError, TrackingLock
from tracker.monitor import get_foreground_info, run_tracking
from tracker.privacy import UrlPolicy, apply_retention
from tracker.utils import fmt_hms


def _setup_paths():
    root = project_root()
    cfg = load_config(root / "config.json")
    data_dir = root / cfg["data_dir"]
    report_dir = root / cfg["report_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    return root, cfg, data_dir / "usage.db", report_dir


def _open_db(db_path: Path, cfg: dict) -> UsageDB:
    """按配置打开数据库：URL 落库策略来自 config（见 tracker/privacy.py）。"""
    return UsageDB(db_path, url_policy=UrlPolicy.from_config(cfg))


def _acquire_or_explain(lock: TrackingLock) -> bool:
    """获取采集锁；拿不到或被锁失败时打印原因并返回 False（fail-closed）。"""
    try:
        acquired = lock.acquire()
    except LockError as exc:
        print(f"【错误】采集锁不可用，为避免重复统计已取消启动：{exc}")
        print("        确认没有其他采集进程后，可执行 python main.py unlock 手动恢复。")
        return False
    if not acquired:
        print("【警告】已有采集会话在运行（可能是 dashboard 或其他 start）。"
              "为避免重复统计，本次启动已取消。")
        return False
    return True


def _release_or_warn(lock: TrackingLock) -> None:
    try:
        lock.release()
    except LockError as exc:
        print(f"【警告】释放采集锁失败：{exc}")


def cmd_start(args):
    """纯后台采集：不启动 GUI，与 dashboard 互斥（同一把锁）。"""
    _, cfg, db_path, _ = _setup_paths()
    lock = TrackingLock(cfg)
    if not _acquire_or_explain(lock):
        return
    try:
        with _open_db(db_path, cfg) as db:
            removed = apply_retention(db, cfg.get("retention_days", 0))
            if removed:
                print(f"已按保留策略（{cfg['retention_days']} 天）清理 {removed} 条历史记录")
            run_tracking(db, cfg)
    finally:
        _release_or_warn(lock)


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
    _, cfg, db_path, _ = _setup_paths()
    with _open_db(db_path, cfg) as db:
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

    _, cfg, db_path, report_dir = _setup_paths()
    with _open_db(db_path, cfg) as db:
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
    with _open_db(db_path, cfg) as db:
        run_app(db, cfg, report_dir)


def cmd_unlock(args):
    """强制清除采集锁文件（进程已确认退出、但锁残留时使用）。"""
    _, cfg, _, _ = _setup_paths()
    lock = TrackingLock(cfg)
    try:
        existed = lock.force_unlock()
    except LockError as exc:
        print(f"【错误】清除锁文件失败：{exc}")
        return
    if existed:
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


def cmd_doctor(args):
    """自检：残留采集锁、时间重叠记录、数据库体积与保留策略。

    重叠检查原本挂在 GUI 启动路径上做全表自连接，数据量一大就会拖慢
    启动；现在改为按需执行的独立命令，并默认只看最近若干天。
    """
    _, cfg, db_path, _ = _setup_paths()
    print("屏幕使用时间 · 自检")
    print("-" * 52)

    # 1) 采集锁
    lock = TrackingLock(cfg)
    if lock.path.exists():
        try:
            content = lock.path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            content = f"（读取失败：{exc}）"
        print(f"[锁]   发现锁文件 {lock.path}")
        print(f"       内容：{content}")
        print("       确认没有采集进程在运行后，可执行 python main.py unlock 清除")
    else:
        print("[锁]   无残留锁文件")

    # 2) 数据库
    if not db_path.exists():
        print(f"[库]   尚未创建：{db_path}")
        print("-" * 52)
        return

    size_mb = db_path.stat().st_size / 1024 / 1024
    with _open_db(db_path, cfg) as db:
        print(f"[库]   {db_path}")
        print(f"       记录数 {db.count_sessions()}    体积 {size_mb:.2f} MB    "
              f"schema v{db.schema_version()}")

        retention = cfg.get("retention_days", 0)
        policy = db.url_policy
        print(f"[隐私] 保留策略 {retention or '永久'}    "
              f"完整 URL {'保留' if policy.store_full_url else '只存域名'}    "
              f"query {'已剥离' if policy.strip_query else '保留'}")

        since = datetime.now() - timedelta(days=args.days)
        pairs = db.find_overlapping_sessions(since=since, limit=args.limit)
        if pairs:
            print(f"[重叠] 最近 {args.days} 天发现 {len(pairs)} 组时间重叠的会话：")
            for p in pairs[:5]:
                print(f"       #{p['a_id']} {p['a_process']:<16} {p['a_start']} ~ {p['a_end']}")
                print(f"       #{p['b_id']} {p['b_process']:<16} {p['b_start']} ~ {p['b_end']}")
            print("       重叠通常意味着曾同时运行多个采集进程，这段时间的时长会被重复统计。")
        else:
            print(f"[重叠] 最近 {args.days} 天未发现时间重叠的会话")

    print("-" * 52)
    print("自检完成。")


def cmd_backup(args):
    """把数据库备份到指定文件。

    用 SQLite 的在线备份 API 而不是直接复制文件：WAL 模式下最近的记录可能
    还留在 ``-wal`` 文件里，直接 copy 会丢数据。
    """
    _, cfg, db_path, _ = _setup_paths()
    if not db_path.exists():
        print(f"数据库不存在，无需备份：{db_path}")
        return

    if args.out:
        target = Path(args.out)
    else:
        target = db_path.with_name(f"{db_path.name}.bak-{datetime.now():%Y%m%d_%H%M%S}")
    target.parent.mkdir(parents=True, exist_ok=True)

    with _open_db(db_path, cfg) as db:
        with sqlite3.connect(str(target)) as dest:
            db.conn.backup(dest)

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"已备份到：{target}（{size_mb:.2f} MB）")


def cmd_restore(args):
    """从备份文件恢复数据库（会覆盖当前数据库）。"""
    _, cfg, db_path, _ = _setup_paths()
    source = Path(args.source)
    if not source.exists():
        print(f"备份文件不存在：{source}")
        return

    if db_path.exists() and not args.force:
        print(f"目标数据库已存在：{db_path}")
        print("恢复会覆盖当前数据。确认后请加 --force 重试；"
              "建议先执行 python main.py backup 保留现状。")
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as src:
        with _open_db(db_path, cfg) as db:
            src.backup(db.conn)

    with sqlite3.connect(str(db_path)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    print(f"已从 {source} 恢复，共 {total} 条记录")


def cmd_demo(args):
    from tracker.demo import seed_demo_data

    _, cfg, db_path, _ = _setup_paths()
    with _open_db(db_path, cfg) as db:
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
    p_doctor = sub.add_parser("doctor", help="自检：残留锁 / 重叠记录 / 数据库体积")
    p_doctor.add_argument("--days", type=int, default=7, help="重叠检查回溯天数（默认 7）")
    p_doctor.add_argument("--limit", type=int, default=20, help="最多列出多少组重叠（默认 20）")
    p_backup = sub.add_parser("backup", help="备份数据库（含 WAL 中的最新数据）")
    p_backup.add_argument("--out", default="", help="备份文件路径，默认 data/usage.db.bak-<时间戳>")
    p_restore = sub.add_parser("restore", help="从备份恢复数据库（会覆盖当前数据）")
    p_restore.add_argument("source", help="备份文件路径")
    p_restore.add_argument("--force", action="store_true", help="确认覆盖当前数据库")
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
        "doctor": cmd_doctor,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "game": cmd_game,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
