"""生成示例数据，便于直接预览图表效果。"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from .db import UsageDB

# (进程名, 显示名, 窗口标题, URL, 分类, 站点key, exe路径)
APPS = [
    ("chrome.exe", "Google Chrome", "哔哩哔哩 (゜-゜)つロ 干杯~-bilibili",
     "https://www.bilibili.com/video/BV1xx411c7mD", "网站", "bilibili.com", ""),
    ("msedge.exe", "Microsoft Edge", "百度一下，你就知道",
     "https://www.baidu.com/", "网站", "baidu.com", ""),
    ("chrome.exe", "Google Chrome", "YouTube",
     "https://www.youtube.com/", "网站", "youtube.com", ""),
    ("chrome.exe", "Google Chrome", "GitHub",
     "https://github.com/", "网站", "github.com", ""),
    ("WeChat.exe", "WeChat.exe", "微信", "", "应用", "", ""),
    ("Code.exe", "Visual Studio Code", "main.py - 屏幕时间 - Visual Studio Code",
     "", "应用", "", ""),
    ("DingTalk.exe", "钉钉", "项目工作群", "", "应用", "", ""),
    ("腾讯会议.exe", "腾讯会议", "项目周会", "", "应用", "", ""),
    ("cloudmusic.exe", "网易云音乐", "网易云音乐", "", "应用", "", ""),
    ("QQ.exe", "QQ", "QQ", "", "应用", "", ""),
    ("cs2.exe", "Counter-Strike 2", "Counter-Strike 2", "", "游戏", "",
     r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\cs2.exe"),
    ("", "锁屏", "", "", "系统", "", ""),
]


def seed_demo_data(db: UsageDB, days: int = 7):
    """为最近 N 天生成模拟使用数据。"""
    random.seed(20260810)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()

    for day_offset in range(days):
        day_start = today - timedelta(days=days - 1 - day_offset)
        horizon = now if day_start.date() == now.date() else day_start.replace(hour=23, minute=30)
        t = day_start.replace(hour=8, minute=30)
        while t < horizon:
            app = random.choice(APPS)
            end = min(t + timedelta(minutes=random.randint(5, 90)), horizon)
            if end <= t:
                break
            db.add_session(t, end, app[1], app[6], app[2], app[4], app[5], app[3])
            t = end + timedelta(minutes=random.randint(2, 30))
    print(f"已为最近 {days} 天生成示例数据")
