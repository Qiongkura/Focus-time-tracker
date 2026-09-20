"""GUI 冒烟测试：真的建一次窗口，逐页切过去。

拆分 app.py 时，静态检查能挡住「名字没导入」，但挡不住「运行到某个页面才炸」
的错误（例如某个 mixin 依赖了别处才初始化的属性）。这个测试直接把
``ScreenTimeApp`` 构造出来、逐页切换并刷新一次。

两点注意：

* 必须屏蔽 ``_spawn_background`` / ``_setup_tray``——否则测试会真的拉起一个
  采集子进程、还会往真实数据目录里写锁文件。
* 没有可用显示环境时直接跳过（例如无头容器）。
"""
from __future__ import annotations

import time

import pytest

from tracker.config import DEFAULTS

tk = pytest.importorskip("tkinter", reason="需要 tkinter")
pytest.importorskip("matplotlib", reason="统计页需要 matplotlib")

from tracker import app as app_module  # noqa: E402
from tracker.db import UsageDB  # noqa: E402

PAGES = ["home", "stats", "records", "categories", "settings"]

# 同一进程里反复创建/销毁 Tk root 时，Windows 上偶尔会瞬时抛 TclError。
# 这不是代码问题，重试一次即可；两次都失败才认定环境没有可用显示。
_ROOT_ATTEMPTS = 2


def _build_app(tmp_path):
    db = UsageDB(tmp_path / "usage.db")
    cfg = dict(DEFAULTS)
    cfg["data_dir"] = str(tmp_path / "data")
    return app_module.ScreenTimeApp(db, cfg, tmp_path / "reports")


@pytest.fixture
def gui_app(tmp_path, monkeypatch):
    # 屏蔽有副作用的部分：不能真的起采集进程、不能装系统托盘
    monkeypatch.setattr(app_module.ScreenTimeApp, "_spawn_background", lambda self: None)
    monkeypatch.setattr(app_module.ScreenTimeApp, "_setup_tray", lambda self: None)

    last_error = None
    for attempt in range(_ROOT_ATTEMPTS):
        try:
            app = _build_app(tmp_path)
            break
        except tk.TclError as exc:
            last_error = exc
            if attempt + 1 < _ROOT_ATTEMPTS:
                time.sleep(0.2)
    else:
        pytest.skip(f"无法创建 Tk 窗口：{last_error}")

    try:
        yield app
    finally:
        try:
            app._preview.stop()
            app.root.destroy()
        except tk.TclError:
            pass


def test_window_constructs(gui_app):
    assert gui_app.root.winfo_exists()
    assert gui_app.current_page == "home"


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_and_refreshes(gui_app, page):
    gui_app.show_page(page)
    gui_app.root.update_idletasks()
    gui_app.refresh()
    assert gui_app.current_page == page


def test_switching_back_and_forth_is_stable(gui_app):
    for _ in range(2):
        for page in PAGES:
            gui_app.show_page(page)
            gui_app.root.update_idletasks()


def test_all_pages_are_registered(gui_app):
    assert set(PAGES) <= set(gui_app.pages)


def test_shutdown_is_idempotent(gui_app):
    gui_app.shutdown()
    gui_app.shutdown()
