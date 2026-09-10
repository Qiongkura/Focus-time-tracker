"""站点清洗与时长格式化单测。"""
from __future__ import annotations

from tracker.utils import clean_site_text, fmt_hms, fmt_minsec, normalize_site_key


def test_clean_tab_suffix():
    assert clean_site_text("页面A - 和另外 3 个页面 - Microsoft Edge") == "页面A"
    assert clean_site_text("Bilibili 视频 - 个人 - Microsoft Edge") == "Bilibili 视频"


def test_normalize_site_key():
    assert normalize_site_key("https://www.bilibili.com/video/BV1xx") == "bilibili.com"
    assert normalize_site_key("www.youtube.com/watch?v=abc") == "youtube.com"
    assert normalize_site_key("GitHub") == "github"


def test_fmt():
    assert fmt_hms(3661) == "01:01:01"
    assert fmt_minsec(65) == "1分钟5秒"
    assert fmt_minsec(3700) == "1小时1分钟"
