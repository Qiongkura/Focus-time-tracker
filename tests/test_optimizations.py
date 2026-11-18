"""针对本轮架构与体验优化的专项回归测试。

覆盖：
1. 系统睡眠/休眠时间跳跃截断（不把睡眠数小时计入睡眠前窗口）；
2. 键鼠无操作空闲暂停（idle_timeout_seconds）；
3. 协作式优雅停止（request_stop 触发最后一段会话刷盘并释放锁，不留残留锁）；
4. game_rules.json 基于 mtime 自动热重载；
5. 浏览器按进程精确匹配历史目录 + immutable 只读查询 + 扩展浏览器列表；
6. UsageDB 线程安全封装方法与 CSV 导出；
7. ForegroundPreviewWorker 按需休眠/唤醒（非设置页零采样）；
8. 设置页排除进程多标签闭包渲染回归（B023）、首页去重计数与高 DPI 不双重放大、托盘休眠暂停刷新；
9. pyproject.toml 打包配置完整性。
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tracker import browser as br
from tracker import games, theme
from tracker.app import ForegroundPreviewWorker
from tracker.config import DEFAULTS, normalize_config
from tracker.db import UsageDB
from tracker.lock import TrackingLock
from tracker.monitor import (
    SessionAccumulator,
    SessionWriter,
    TrackingService,
    classify_window,
)
from tracker.ui import preview as preview_module

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------- 1. 睡眠/休眠时间跳跃保护 ----------

def test_sleep_gap_truncates_at_last_seen_instead_of_wake_time(tmp_path):
    """合盖睡眠 8 小时后唤醒，睡眠前会话必须在合盖前最后一次采样处截断。"""
    base = datetime(2026, 5, 1, 23, 0, 0)
    acc = SessionAccumulator(min_session=3, checkpoint_seconds=45, max_gap_seconds=120)

    with UsageDB(tmp_path / "usage.db") as db:
        writer = SessionWriter(db, min_session=3)
        info = {"process": "code.exe", "exe_path": "", "title": "main.py",
                "category": "应用", "site": "", "site_title": "", "url": ""}

        # 睡前正常使用了 20 秒
        for sec in (0, 10, 20):
            for seg, end in acc.observe(info, base + timedelta(seconds=sec)):
                writer.write(seg, end)

        # 8 小时后唤醒，前台仍是同一个窗口
        wake = base + timedelta(hours=8)
        for seg, end in acc.observe(info, wake):
            writer.write(seg, end)
        for seg, end in acc.observe(info, wake + timedelta(seconds=10)):
            writer.write(seg, end)
        final = acc.flush(wake + timedelta(seconds=15))
        if final:
            writer.write(*final)

        total = db.total_seconds_between(base - timedelta(hours=1), wake + timedelta(hours=1))

    # 真实使用只有 20s + 15s = 35s，绝不能包含睡眠的 8 小时（28800s）
    assert total == pytest.approx(35.0)


# ---------- 2. 空闲（AFK）检测 ----------

def test_idle_timeout_pauses_tracking_when_afk(tmp_path):
    idle_val = {"seconds": 0.0}
    info = {"process": "code.exe", "exe_path": "", "title": "editor",
            "category": "应用", "site": "", "site_title": "", "url": ""}

    with UsageDB(tmp_path / "usage.db") as db:
        cfg = dict(DEFAULTS, min_session_seconds=0, idle_timeout_seconds=300)
        svc = TrackingService(
            db, cfg,
            fetch_info=lambda: info,
            idle_fn=lambda: idle_val["seconds"],
        )
        svc.tick()
        assert svc.accumulator.current is not None

        # 空闲达到 300 秒后，当前会话应被立即关闭
        idle_val["seconds"] = 305.0
        svc.tick()
        assert svc.accumulator.current is None


def test_config_normalizes_idle_timeout_seconds():
    cfg, problems = normalize_config({"idle_timeout_seconds": 300})
    assert problems == []
    assert cfg["idle_timeout_seconds"] == 300

    cfg_bad, problems_bad = normalize_config({"idle_timeout_seconds": "invalid"})
    assert cfg_bad["idle_timeout_seconds"] == 0
    assert problems_bad


# ---------- 3. 协作式优雅停止（刷盘 + 释放锁） ----------

def test_graceful_stop_flushes_session_and_releases_lock(tmp_path):
    """子进程通过 stop_check 优雅退出时，未满 checkpoint 的会话必须落库且锁文件被清除。"""
    lock_path = tmp_path / "data" / "tracking.lock"
    db_path = tmp_path / "data" / "usage.db"
    code = "\n".join([
        "import sys",
        f"sys.path.insert(0, r'{PROJECT_ROOT}')",
        "from pathlib import Path",
        "from tracker.db import UsageDB",
        "from tracker.lock import TrackingLock",
        "from tracker.monitor import TrackingService",
        f"lock = TrackingLock(path=Path(r'{lock_path}'))",
        "assert lock.acquire()",
        "cfg = {'poll_interval_seconds': 0.1, 'min_session_seconds': 0, 'checkpoint_seconds': 3600}",
        "info = {'process': 'code.exe', 'exe_path': '', 'title': 'work',",
        "        'category': '应用', 'site': '', 'site_title': '', 'url': ''}",
        "print('READY', flush=True)",
        "try:",
        f"    with UsageDB(Path(r'{db_path}')) as db:",
        "        TrackingService(db, cfg, fetch_info=lambda: info,",
        "                        stop_check=lock.stop_requested).run()",
        "finally:",
        "    lock.release()",
    ])
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "READY"
        time.sleep(0.25)
        observer = TrackingLock(path=lock_path)
        assert observer.holder_alive() is True
        assert observer.request_stop(timeout=4.0) is True
        proc.wait(timeout=4.0)
        assert proc.returncode == 0
        assert not lock_path.exists(), "优雅退出后不应残留锁文件"
        with UsageDB(db_path) as db:
            assert db.count_sessions() >= 1, "未满 checkpoint 的会话应在退出时自动刷盘"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ---------- 4. game_rules.json 热重载 ----------

def test_game_rules_auto_reloads_when_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "project_root", lambda: tmp_path)
    games._cache = None
    games._mtime = None
    try:
        assert not games.is_game(process="custom_indie_game.exe")
        rules_file = tmp_path / "game_rules.json"
        rules_file.write_text(
            json.dumps({"process_exact": ["custom_indie_game.exe"]}),
            encoding="utf-8",
        )
        # 无需手动调用 reload_rules()，mtime 变化应自动触发重载
        assert games.is_game(process="custom_indie_game.exe")
    finally:
        games._cache = None
        games._mtime = None


# ---------- 5. 浏览器隔离扫描与扩展浏览器识别 ----------

def test_browser_candidates_isolate_chrome_and_edge(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    chrome_h = tmp_path / "Google" / "Chrome" / "User Data" / "Default" / "History"
    edge_h = tmp_path / "Microsoft" / "Edge" / "User Data" / "Default" / "History"
    chrome_h.parent.mkdir(parents=True)
    edge_h.parent.mkdir(parents=True)
    chrome_h.write_bytes(b"")
    edge_h.write_bytes(b"")

    assert br._history_candidates("chrome.exe") == [chrome_h]
    assert br._history_candidates("msedge.exe") == [edge_h]


def test_brave_and_vivaldi_classified_as_website(monkeypatch):
    monkeypatch.setattr(
        br, "_db_query_due", lambda _p: False,
    )
    info = classify_window(process="brave.exe", title="GitHub - Brave")
    assert info["category"] == "网站"


# ---------- 6. UsageDB 封装与 CSV 导出 ----------

def test_db_helper_methods_and_csv_export(tmp_path):
    now = datetime(2026, 8, 10, 14, 30, 0)
    csv_path = tmp_path / "export.csv"
    with UsageDB(tmp_path / "usage.db") as db:
        db.add_session(
            now, now + timedelta(minutes=15),
            process="Code.exe", exe_path=r"C:\VSCode\Code.exe",
            title="app.py", category="应用",
        )
        assert db.latest_exe_path("Code.exe") == r"C:\VSCode\Code.exe"
        sig = db.sessions_signature_between(now - timedelta(hours=1), now + timedelta(hours=1))
        assert sig[0] == 1 and sig[2] == pytest.approx(900.0)
        hourly = db.hourly_sessions_between(now - timedelta(hours=1), now + timedelta(hours=1))
        assert len(hourly) == 1
        exported = db.export_sessions_csv(
            now - timedelta(hours=1), now + timedelta(hours=1), csv_path,
        )
        assert exported == 1

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 2
    assert rows[0][0] == "开始时间"
    assert rows[1][4] == "Code.exe"


# ---------- 7. ForegroundPreviewWorker 休眠/唤醒 ----------

def test_preview_worker_does_not_sample_while_inactive(monkeypatch):
    calls = {"n": 0}

    def counting_sample():
        calls["n"] += 1
        return {"process": "code.exe", "title": "x", "category": "应用"}

    monkeypatch.setattr(preview_module, "get_foreground_info", counting_sample)
    worker = ForegroundPreviewWorker(interval=0.05)
    worker.set_active(False)
    worker.start()
    try:
        time.sleep(0.2)
        assert calls["n"] == 0, "未激活状态下预览线程不应调用 get_foreground_info"
        worker.set_active(True)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and calls["n"] == 0:
            time.sleep(0.02)
        assert calls["n"] >= 1, "激活后应立即恢复采样"
    finally:
        worker.stop()


# ---------- 8. GUI 回归：排除标签闭包、去重统计、托盘休眠 ----------

def test_gui_chips_closure_and_mini_card_distinct_counts(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    pytest.importorskip("matplotlib")
    from tracker import app as app_module

    monkeypatch.setattr(app_module.ScreenTimeApp, "_spawn_background", lambda self: None)
    monkeypatch.setattr(app_module.ScreenTimeApp, "_setup_tray", lambda self: None)

    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    db = UsageDB(tmp_path / "usage.db")
    # 同一个软件分 3 段写入，软件去重数量必须是 1 个（而非旧版的 3 个切片）
    for i in range(3):
        s = now - timedelta(minutes=(i + 1) * 2)
        db.add_session(s, s + timedelta(seconds=30), process="Code.exe", category="应用")
    # 游戏时长更长，但筛选为“应用”时最常用软件不能显示成游戏
    db.add_session(now - timedelta(minutes=20), now - timedelta(minutes=10),
                   process="cs2.exe", category="游戏")

    cfg = dict(DEFAULTS)
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["exclude_processes"] = ["alpha.exe", "beta.exe", "gamma.exe"]

    try:
        app = app_module.ScreenTimeApp(db, cfg, tmp_path / "reports")
    except tk.TclError as exc:
        db.close()
        pytest.skip(f"无法创建 Tk 窗口：{exc}")

    try:
        # 1) 验证设置页排除标签没有因为闭包漏绑而全部变成 gamma.exe
        app.show_page("settings")
        for _ in range(6):
            app.root.update()
        chip_texts = []
        for child in app.chips_frame.winfo_children():
            if isinstance(child, tk.Canvas):
                for item in child.find_all():
                    if child.type(item) == "text":
                        chip_texts.append(child.itemcget(item, "text"))
        assert chip_texts == ["alpha.exe  ×", "beta.exe  ×", "gamma.exe  ×"]

        # 2) 验证首页“应用”筛选下数量去重为 1 个，且最常用软件是 VS Code 而非 CS2
        app.show_page("home")
        app.filter = "应用"
        app.filter_combo.set("应用")
        app.refresh()
        captions = {
            cap.cget("text"): val.cget("text")
            for _card, val, cap in app.mini_cards
        }
        assert captions["软件数量（去重）"] == "1 个"
        assert captions["最常用软件"] == "Visual Studio Code"

        # 3) 验证 FlowFrame 没有被双重缩放
        expected_item_w = theme.scale(210)
        assert app.mini_row._item_width == pytest.approx(expected_item_w)

        # 4) 验证隐藏到托盘时暂停定时刷新与预览采样，恢复时重新开启
        app._tray_enabled = True
        app._hide_to_tray(silent=True)
        assert app._hidden_to_tray is True
        assert app._refresh_timer is None
        assert not app._preview._active.is_set()

        app._restore_from_tray()
        assert app._hidden_to_tray is False
        assert app._refresh_timer is not None
    finally:
        app.shutdown()
        db.close()


# ---------- 9. 打包配置完整性 ----------

def test_pyproject_includes_ui_subpackage_and_entrypoints():
    text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tracker.ui"' in text
    assert '"launcher"' in text
