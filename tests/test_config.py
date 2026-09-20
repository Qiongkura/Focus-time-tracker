"""配置加载与校验：坏配置必须被就地修正并报告，而不是让采集链路出错。"""
from __future__ import annotations

import json
from pathlib import Path

from tracker.config import (
    DEFAULTS,
    MIN_POLL_INTERVAL,
    load_config,
    load_config_detailed,
    normalize_config,
)


def _write(path: Path, payload) -> Path:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


# ---------- 文件级异常 ----------

def test_missing_file_uses_defaults(tmp_path):
    assert load_config(tmp_path / "nope.json") == DEFAULTS


def test_broken_json_falls_back_to_defaults(tmp_path):
    path = _write(tmp_path / "config.json", "{ this is not json")
    cfg, problems = load_config_detailed(path)
    assert cfg == DEFAULTS
    assert problems            # 必须被报告出来，而不是静默吞掉


def test_non_object_root_falls_back(tmp_path):
    path = _write(tmp_path / "config.json", [1, 2, 3])
    cfg, problems = load_config_detailed(path)
    assert cfg == DEFAULTS
    assert problems


def test_defaults_are_not_shared_between_calls(tmp_path):
    first = load_config(tmp_path / "nope.json")
    first["exclude_processes"].append("mutated.exe")
    second = load_config(tmp_path / "nope.json")
    assert second["exclude_processes"] == []
    assert DEFAULTS["exclude_processes"] == []


# ---------- 数值项 ----------

def test_zero_poll_interval_is_clamped(tmp_path):
    path = _write(tmp_path / "config.json", {"poll_interval_seconds": 0})
    cfg, problems = load_config_detailed(path)
    assert cfg["poll_interval_seconds"] == MIN_POLL_INTERVAL
    assert problems


def test_negative_poll_interval_is_clamped(tmp_path):
    path = _write(tmp_path / "config.json", {"poll_interval_seconds": -5})
    assert load_config(path)["poll_interval_seconds"] == MIN_POLL_INTERVAL


def test_non_numeric_interval_falls_back(tmp_path):
    path = _write(tmp_path / "config.json", {"poll_interval_seconds": "fast"})
    assert load_config(path)["poll_interval_seconds"] == DEFAULTS["poll_interval_seconds"]


def test_boolean_interval_is_rejected(tmp_path):
    # JSON 的 true 会被读成 bool；float(True) == 1.0，但语义明显不对
    path = _write(tmp_path / "config.json", {"poll_interval_seconds": True})
    assert load_config(path)["poll_interval_seconds"] == DEFAULTS["poll_interval_seconds"]


def test_negative_min_session_is_clamped(tmp_path):
    path = _write(tmp_path / "config.json", {"min_session_seconds": -1})
    assert load_config(path)["min_session_seconds"] == 0.0


def test_checkpoint_below_min_session_is_raised(tmp_path):
    path = _write(tmp_path / "config.json",
                  {"min_session_seconds": 30, "checkpoint_seconds": 5})
    cfg, problems = load_config_detailed(path)
    assert cfg["checkpoint_seconds"] >= cfg["min_session_seconds"]
    assert problems


# ---------- 进程排除列表 ----------

def test_exclude_processes_string_becomes_list(tmp_path):
    path = _write(tmp_path / "config.json", {"exclude_processes": "python.exe"})
    cfg, problems = load_config_detailed(path)
    assert cfg["exclude_processes"] == ["python.exe"]
    assert problems


def test_exclude_processes_is_lowercased_and_trimmed(tmp_path):
    path = _write(tmp_path / "config.json",
                  {"exclude_processes": ["Chrome.exe", "  MSEDGE.EXE  ", ""]})
    assert load_config(path)["exclude_processes"] == ["chrome.exe", "msedge.exe"]


def test_exclude_processes_dedupes(tmp_path):
    path = _write(tmp_path / "config.json",
                  {"exclude_processes": ["a.exe", "A.EXE", "a.exe"]})
    assert load_config(path)["exclude_processes"] == ["a.exe"]


def test_exclude_processes_wrong_type_becomes_empty(tmp_path):
    path = _write(tmp_path / "config.json", {"exclude_processes": 123})
    assert load_config(path)["exclude_processes"] == []


# ---------- 布尔项 ----------

def test_browser_site_tracking_string_is_parsed(tmp_path):
    path = _write(tmp_path / "config.json", {"browser_site_tracking": "false"})
    assert load_config(path)["browser_site_tracking"] is False


def test_browser_site_tracking_bad_value_falls_back(tmp_path):
    path = _write(tmp_path / "config.json", {"browser_site_tracking": "maybe"})
    assert load_config(path)["browser_site_tracking"] is DEFAULTS["browser_site_tracking"]


# ---------- 路径项 ----------

def test_blank_data_dir_falls_back(tmp_path):
    path = _write(tmp_path / "config.json", {"data_dir": "   "})
    assert load_config(path)["data_dir"] == DEFAULTS["data_dir"]


def test_absolute_data_dir_is_kept(tmp_path):
    target = str(tmp_path / "custom-data")
    path = _write(tmp_path / "config.json", {"data_dir": target})
    assert load_config(path)["data_dir"] == target


# ---------- 未知键与正常配置 ----------

def test_unknown_keys_are_reported(tmp_path):
    path = _write(tmp_path / "config.json", {"nope": 1})
    cfg, problems = load_config_detailed(path)
    assert cfg == DEFAULTS
    assert any("nope" in p for p in problems)


def test_valid_config_is_untouched(tmp_path):
    path = _write(tmp_path / "config.json", {
        "poll_interval_seconds": 2.5,
        "min_session_seconds": 5,
        "checkpoint_seconds": 60,
        "exclude_processes": ["explorer.exe"],
        "browser_site_tracking": False,
        "data_dir": "mydata",
        "report_dir": "myreports",
    })
    cfg, problems = load_config_detailed(path)
    assert problems == []
    assert cfg["poll_interval_seconds"] == 2.5
    assert cfg["exclude_processes"] == ["explorer.exe"]
    assert cfg["browser_site_tracking"] is False
    assert cfg["data_dir"] == "mydata"
    assert cfg["report_dir"] == "myreports"


def test_normalize_config_accepts_empty_dict():
    cfg, problems = normalize_config({})
    assert cfg == DEFAULTS
    assert problems == []
