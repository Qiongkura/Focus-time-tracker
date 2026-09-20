"""设置页与后台管理拆分后的契约测试。

设置页 / 后台托管已抽到 `tracker/ui/settings_page.py` 与
`tracker/ui/background.py`。这里锁住：

1. mixin 契约——`ScreenTimeApp` 仍暴露全部方法；
2. `_background_running` 改为复用采集锁后的语义；
3. `_persist_exclude` 的落盘格式。
"""
from __future__ import annotations

import json
import os

import pytest

from tracker.app import ScreenTimeApp
from tracker import lock as lock_module
from tracker.lock import process_created_time
from tracker.ui import BackgroundMixin, SettingsPageMixin
from tracker.ui import background as bg_module
from tracker.ui import settings_page as settings_module

SETTINGS_METHODS = [
    "_build_settings",
    "_save_settings",
    "_add_exclude",
    "_remove_exclude",
    "_rebuild_chips",
    "_persist_exclude",
    "_clear_tracking_lock",
    "_refresh_settings_live",
]

BACKGROUND_METHODS = [
    "_spawn_background",
    "_stop_background_process",
    "_setup_tray",
    "_restore_from_tray",
    "_check_overlap_sessions",
    "_background_running",
]


# ---------- mixin 契约 ----------

@pytest.mark.parametrize("name", SETTINGS_METHODS)
def test_app_exposes_settings_method(name):
    assert hasattr(ScreenTimeApp, name), f"拆分后丢失方法：{name}"


@pytest.mark.parametrize("name", BACKGROUND_METHODS)
def test_app_exposes_background_method(name):
    assert hasattr(ScreenTimeApp, name), f"拆分后丢失方法：{name}"


def test_both_mixins_are_in_mro():
    assert SettingsPageMixin in ScreenTimeApp.__mro__
    assert BackgroundMixin in ScreenTimeApp.__mro__


def test_mixins_own_their_methods():
    assert "_build_settings" in vars(SettingsPageMixin)
    assert "_background_running" in vars(BackgroundMixin)


# ---------- _background_running ----------

class _FakeBackground(BackgroundMixin):
    def __init__(self, cfg):
        self.cfg = cfg
        self.errors = []

    def _log_error(self, where, exc):
        self.errors.append((where, exc))


@pytest.fixture
def bg_env(tmp_path, monkeypatch):
    """把 background 与 lock 两个模块的 project_root 都指向 tmp_path。

    注意必须连 `tracker.lock` 一起打桩：`TrackingLock(cfg)` 是在 lock 模块
    内部解析锁路径的，只改 background 的 project_root 会让查询落到真实项目
    目录上，测试就变成了空跑。
    """
    monkeypatch.setattr(bg_module, "project_root", lambda: tmp_path)
    monkeypatch.setattr(lock_module, "project_root", lambda: tmp_path)
    lock_path = tmp_path / "data" / "tracking.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    return _FakeBackground({"data_dir": "data"}), lock_path


def test_background_not_running_without_lock(bg_env):
    app, _lock_path = bg_env
    assert app._background_running() is False


def test_background_running_for_live_holder(bg_env):
    app, lock_path = bg_env
    _alive, created = process_created_time(os.getpid())
    lock_path.write_text(f"{os.getpid()}|{created}", encoding="utf-8")

    assert app._background_running() is True


def test_background_not_running_for_dead_pid(bg_env):
    """关键回归：PID 不存在时必须报「未运行」，否则界面永远不会重新拉起采集。"""
    app, lock_path = bg_env
    lock_path.write_text("999999999|0", encoding="utf-8")

    assert app._background_running() is False


def test_background_not_running_for_corrupted_lock(bg_env):
    app, lock_path = bg_env
    lock_path.write_text("not-a-pid", encoding="utf-8")

    assert app._background_running() is False


def test_background_running_returns_false_on_lock_error(bg_env, monkeypatch):
    """锁文件读不了时不应抛异常打断界面刷新。"""
    app, _lock_path = bg_env

    class _Boom:
        def __init__(self, cfg):
            pass

        def holder_alive(self):
            from tracker.lock import LockError
            raise LockError("读不了")

    monkeypatch.setattr(bg_module, "TrackingLock", _Boom)

    assert app._background_running() is False


# ---------- _persist_exclude ----------

def test_persist_exclude_writes_readable_json(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "project_root", lambda: tmp_path)
    app = type("A", (SettingsPageMixin,), {})()
    app.cfg = {"exclude_processes": ["code.exe", "微信"], "poll_interval_seconds": 1.0}

    app._persist_exclude()

    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["exclude_processes"] == ["code.exe", "微信"]
    # ensure_ascii=False：中文要原样落盘，便于用户手改
    assert "微信" in (tmp_path / "config.json").read_text(encoding="utf-8")
