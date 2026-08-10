"""配置加载与项目路径。"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "poll_interval_seconds": 1.0,   # 前台窗口采样间隔（秒）
    "min_session_seconds": 3,       # 低于该时长的小片段不记录
    "exclude_processes": [],        # 不想统计的进程名列表，如 ["explorer.exe"]
    "browser_site_tracking": True,  # 识别浏览器当前访问的具体网站
    "data_dir": "data",             # SQLite 数据目录
    "report_dir": "reports",        # 图表输出目录
}


def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def project_root() -> Path:
    """项目根目录（main.py 所在目录）。"""
    return Path(__file__).resolve().parent.parent
