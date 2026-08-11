"""用户手动分类覆盖：进程名 -> 分类（应用/游戏），优先级高于自动规则引擎。

覆盖规则存放在项目根目录 category_overrides.json（打包后为 exe 同目录），
由 GUI 分类页的「移到游戏 / 移到应用 / 还原自动」按钮读写。
采集进程每次判断前台窗口时都会检测文件变更，改完立即生效，无需重启。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import project_root

OVERRIDES_FILENAME = "category_overrides.json"
VALID_CATEGORIES = ("应用", "游戏")

_lock = threading.Lock()
_cache: dict[str, str] | None = None
_mtime: int | None = None


def overrides_path() -> Path:
    return project_root() / OVERRIDES_FILENAME


def _normalize(process: str) -> str:
    return (process or "").strip().lower()


def load_overrides(path: Path | None = None) -> dict[str, str]:
    """读取覆盖文件，只保留合法的 {进程名小写: 应用|游戏} 条目。"""
    path = Path(path) if path is not None else overrides_path()
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return result
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        name = _normalize(key)
        if name and str(value) in VALID_CATEGORIES:
            result[name] = str(value)
    return result


def _refresh_if_changed() -> dict[str, str]:
    """文件 mtime 变化时重新加载；否则用进程内缓存（避免每秒读文件）。"""
    global _cache, _mtime
    path = overrides_path()
    try:
        current = path.stat().st_mtime_ns
    except OSError:
        current = None
    if _cache is None or current != _mtime:
        _cache = load_overrides()
        _mtime = current
    return _cache


def override_for(process: str) -> str | None:
    """返回该进程的手动分类（应用/游戏），无覆盖时返回 None。"""
    return _refresh_if_changed().get(_normalize(process))


def all_overrides() -> dict[str, str]:
    return dict(_refresh_if_changed())


def _save(data: dict[str, str]) -> None:
    path = overrides_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def set_override(process: str, category: str) -> bool:
    """记录「进程 -> 分类」覆盖；非法分类或空进程名返回 False。"""
    global _cache, _mtime
    name = _normalize(process)
    if not name or category not in VALID_CATEGORIES:
        return False
    with _lock:
        data = load_overrides()
        data[name] = category
        try:
            _save(data)
        except Exception:
            return False
        _cache = data
        try:
            _mtime = overrides_path().stat().st_mtime_ns
        except OSError:
            _mtime = None
        return True


def remove_override(process: str) -> bool:
    """清除某进程的手动覆盖；不存在时视为成功（幂等）。"""
    global _cache, _mtime
    name = _normalize(process)
    with _lock:
        data = load_overrides()
        if name in data:
            del data[name]
            try:
                _save(data)
            except Exception:
                return False
        _cache = data
        try:
            _mtime = overrides_path().stat().st_mtime_ns
        except OSError:
            _mtime = None
        return True
