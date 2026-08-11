"""游戏识别规则引擎（按“启动器/安装目录”识别，不靠逐个加游戏名）。

判断优先级（命中即停）：
1. 排除规则（exclude_*）命中 -> 不是游戏，防止目录关键字误伤普通软件
2. 安装目录关键字（path_keywords，如 steamapps / riot games / epic games）
3. 进程名精确匹配（process_exact，如 valorant.exe）
4. 进程名后缀特征（process_suffix，如 -win64-shipping.exe，覆盖大量 UE 引擎游戏）

用户可在项目根目录的 game_rules.json 中增删规则（默认向内置规则追加、自动去重；
设置 "replace_defaults": true 可改为整体替换内置列表），
打包版则读取 exe 同目录的 game_rules.json。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from .config import project_root

GAME_RULES_FILENAME = "game_rules.json"

# 内置默认规则；game_rules.json 中同名键会整体覆盖对应列表
DEFAULT_RULES = {
    # 启动器 / 发行平台安装目录关键字（命中即视为游戏，覆盖该目录下所有游戏）
    "path_keywords": [
        "steamapps",          # Steam（含其他盘符的 SteamLibrary）
        "riot games",         # 拳头：无畏契约 / 英雄联盟 等
        "epic games",         # Epic 启动器游戏
        "battle.net",         # 暴雪战网
        "battlenet",
        "blizzard",           # 旧版暴雪安装目录
        "ubisoft",            # 育碧（Ubisoft Connect）
        "ea games",           # EA App 游戏目录
        "electronic arts",    # 旧版 EA 目录
        "gog galaxy",         # GOG Galaxy
        "wegame",             # 腾讯 WeGame
        "rail_apps",          # WeGame 联运游戏目录
        "hoyoplay",           # 米哈游新启动器（HoYoPlay）
        "mihoyo",             # 米哈游旧启动器目录
        "hoyoverse",
        "rockstar games",     # R 星（GTA / 荒野大镖客）
        "2k games",           # 2K
        "netease",            # 网易 PC 游戏（配合下方排除规则防误伤）
    ],
    # 进程名精确匹配（小写、带 .exe；仅作兜底，常规游戏靠安装目录规则覆盖）
    "process_exact": [
        "valorant.exe",
        "leagueclient.exe",
        "league of legends.exe",
        "genshinimpact.exe",
        "starrail.exe",
    ],
    # 进程名后缀特征：Unreal Engine 发行版客户端统一命名，覆盖三角洲行动等大量 UE 游戏
    "process_suffix": [
        "-win64-shipping.exe",
        "-win32-shipping.exe",
    ],
    # 目录里出现这些关键字即使命中 path_keywords 也不算游戏（防误伤普通软件）
    "exclude_path_keywords": [
        "cloudmusic",         # 网易云音乐
        "youdao",             # 有道词典
        "wechat",             # 微信
        "qq\\",               # QQ（如用户自行加了 tencent 关键字）
    ],
    # 进程名精确排除（兜底防误判）
    "exclude_process_exact": [],
}

_lock = threading.Lock()
_cache: dict | None = None


def load_rules(path: Path | None = None) -> dict:
    """加载规则：内置默认值 + 用户 game_rules.json（默认追加，replace_defaults 时整体替换）。"""
    rules = {key: list(values) for key, values in DEFAULT_RULES.items()}
    if path is None:
        path = project_root() / GAME_RULES_FILENAME
    path = Path(path)
    if not path.exists():
        return rules
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = json.load(f)
    except Exception:
        return rules
    if not isinstance(user, dict):
        return rules
    replace = bool(user.get("replace_defaults", False))
    for key, values in user.items():
        if key == "replace_defaults" or not isinstance(values, list):
            continue
        items = [str(v).strip().lower() for v in values if str(v).strip()]
        if replace:
            rules[key] = items
        else:
            existing = rules.get(key, [])
            rules[key] = existing + [v for v in items if v not in existing]
    return rules


def _rules() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            _cache = load_rules()
        return _cache


def reload_rules() -> dict:
    """重新加载规则（改完 game_rules.json 后调用，供测试使用）。"""
    global _cache
    with _lock:
        _cache = load_rules()
        return _cache


def is_game(exe_path: str = "", process: str = "", title: str = "") -> bool:
    """判断某个前台窗口是否属于游戏。参数均可为空，尽量提供 exe_path 或 process。"""
    rules = _rules()
    exe = (exe_path or "").lower().replace("/", "\\")
    proc = (process or "").lower().strip()

    if any(k in exe for k in rules.get("exclude_path_keywords", [])):
        return False
    if proc in rules.get("exclude_process_exact", []):
        return False
    if any(k in exe for k in rules.get("path_keywords", [])):
        return True
    if proc in rules.get("process_exact", []):
        return True
    if any(proc.endswith(suf) for suf in rules.get("process_suffix", [])):
        return True
    return False
