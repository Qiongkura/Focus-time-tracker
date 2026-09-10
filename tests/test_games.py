"""游戏识别规则引擎单测（不依赖 Win32）。"""
from __future__ import annotations

import json

from tracker.games import is_game, load_rules, reload_rules


def test_path_keywords_hit():
    assert is_game(exe_path=r"C:\SteamLibrary\steamapps\common\Game\game.exe",
                   process="game.exe")
    assert is_game(exe_path=r"D:\Riot Games\VALORANT\live\VALORANT.exe",
                   process="VALORANT.exe")


def test_exclude_path_beats_path_keywords():
    # 网易云音乐在排除列表，即便路径里也有 netease 类关键字也不应误判
    assert not is_game(
        exe_path=r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe",
        process="cloudmusic.exe",
    )


def test_process_exact_and_suffix():
    assert is_game(exe_path="", process="valorant.exe")
    assert is_game(exe_path="", process="Foo-Win64-Shipping.exe")


def test_non_game():
    assert not is_game(exe_path=r"C:\Program Files\Google\Chrome\chrome.exe",
                       process="chrome.exe")
    assert not is_game(exe_path="", process="notepad.exe")


def test_user_rules_append(tmp_path):
    rules_path = tmp_path / "game_rules.json"
    rules_path.write_text(json.dumps({
        "path_keywords": ["my_custom_games"],
    }), encoding="utf-8")
    rules = load_rules(rules_path)
    assert "steamapps" in rules["path_keywords"]
    assert "my_custom_games" in rules["path_keywords"]
    reload_rules()  # 清理全局缓存影响


def test_replace_defaults(tmp_path):
    rules_path = tmp_path / "game_rules.json"
    rules_path.write_text(json.dumps({
        "replace_defaults": True,
        "path_keywords": ["only_this"],
        "process_exact": [],
        "process_suffix": [],
        "exclude_path_keywords": [],
        "exclude_process_exact": [],
    }), encoding="utf-8")
    rules = load_rules(rules_path)
    assert rules["path_keywords"] == ["only_this"]
    assert "steamapps" not in rules["path_keywords"]
    reload_rules()
