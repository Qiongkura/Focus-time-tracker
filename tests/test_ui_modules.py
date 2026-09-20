"""GUI 模块拆分后的静态完整性检查。

拆分 app.py 时最大的风险不是语法错误（那个 import 就会炸），而是
**某个 mixin 的方法体引用了没导入的名字**——这类代码只有在对应页面真的
被构建时才会抛 ``NameError``，而单测通常不会去建 Tk 窗口，于是静默通过。

首页拆分时就真的踩过一次：搬过来的代码调用 ``_shorten``，而新模块导入的是
``shorten``。这里用一个轻量的名字解析检查把这类问题挡在提交之前。
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

from tracker.app import ScreenTimeApp
from tracker import ui

TRACKER_DIR = Path(__file__).resolve().parent.parent / "tracker"
UI_DIR = TRACKER_DIR / "ui"

BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


# ---------- 名字解析检查 ----------

def _module_bindings(tree: ast.Module) -> set[str]:
    """收集模块顶层绑定的名字（导入、赋值、函数、类）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in _target_names(node.target if not isinstance(node, ast.Assign) else None,
                                        node.targets if isinstance(node, ast.Assign) else None):
                names.add(target)
    return names


def _target_names(single, many):
    targets = many if many is not None else [single]
    for t in targets:
        for sub in ast.walk(t):
            if isinstance(sub, ast.Name):
                yield sub.id


def _checkable_functions(tree: ast.Module):
    """只取「作用域边界」函数：类的方法 + 模块级函数。

    不取嵌套函数——它们能看见外层函数的局部变量，单独检查会产生大量假阳性。
    这些嵌套函数会在所属方法的子树遍历中被一并覆盖。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield item
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _local_bindings(fn) -> set[str]:
    """收集函数体（含嵌套作用域）内所有绑定名字。

    包含参数、赋值、for/with/except、推导式、嵌套定义，以及**嵌套函数与
    lambda 的参数**——后者容易漏，漏了就会把 ``lambda e: e.widget`` 里的
    ``e`` 误报成未定义。
    """
    names: set[str] = set()

    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)          # 含嵌套 def / lambda 的参数
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _undefined_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed_globals = _module_bindings(tree) | BUILTINS
    problems = []

    for fn in _checkable_functions(tree):
        allowed = allowed_globals | _local_bindings(fn)
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in allowed:
                    problems.append(
                        f"{path.name}:{sub.lineno} {fn.name}() 引用了未定义的名字 {sub.id!r}")
    return problems


@pytest.mark.parametrize("path", sorted(UI_DIR.glob("*.py")), ids=lambda p: p.name)
def test_ui_modules_have_no_undefined_names(path):
    assert _undefined_names(path) == []


def test_app_module_has_no_undefined_names():
    assert _undefined_names(TRACKER_DIR / "app.py") == []


# ---------- mixin 组装契约 ----------

EXPECTED_MIXINS = [
    "ShellMixin",
    "HomePageMixin",
    "StatsPageMixin",
    "RecordsPageMixin",
    "CategoriesPageMixin",
    "SettingsPageMixin",
    "RefreshMixin",
    "BackgroundMixin",
]


@pytest.mark.parametrize("name", EXPECTED_MIXINS)
def test_mixin_is_exported_and_in_mro(name):
    assert hasattr(ui, name), f"tracker.ui 未导出 {name}"
    assert getattr(ui, name) in ScreenTimeApp.__mro__, f"{name} 不在 ScreenTimeApp 的 MRO 里"


def test_mixins_do_not_shadow_each_other():
    """两个 mixin 定义同名方法时，MRO 顺序会静默决定谁生效——必须避免。"""
    owners: dict[str, list[str]] = {}
    for name in EXPECTED_MIXINS:
        for attr in vars(getattr(ui, name)):
            if attr.startswith("__"):
                continue
            owners.setdefault(attr, []).append(name)

    conflicts = {a: o for a, o in owners.items() if len(o) > 1}
    assert conflicts == {}, f"跨 mixin 重名：{conflicts}"


def test_app_shell_still_exposes_nav_pages():
    from tracker.ui.app_shell import NAV_PAGES

    keys = [k for k, _, _ in NAV_PAGES]
    assert keys == ["home", "stats", "records", "categories"]


def test_rotate_log_is_reexported_from_app():
    from tracker.app import rotate_log
    from tracker.ui.app_shell import rotate_log as shell_rotate_log

    assert rotate_log is shell_rotate_log


def test_preview_worker_is_reexported_from_app():
    from tracker.app import ForegroundPreviewWorker
    from tracker.ui.preview import ForegroundPreviewWorker as ui_worker

    assert ForegroundPreviewWorker is ui_worker
