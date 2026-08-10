"""通用工具：时长格式化。"""
from __future__ import annotations

import re


_ZERO_WIDTH = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF))
# 多标签窗口标题：从“和另外 N 个页面”开始全部去掉（N 不同不应算不同网站）
_TAB_SUFFIX_RE = re.compile(r"\s*和另外\s*\d+\s*个页面.*$")
# 尾部浏览器名 / 个人 后缀（容忍中间有零宽空格）
_BROWSER_SUFFIX_RE = re.compile(
    r"\s*[-—|·]\s*(?:个人\s*)?(?:谷歌 ?chrome|google chrome|microsoft ?edge|microsoft|"
    r"edge|chrome|firefox|网页|internet explorer)\s*$",
    re.IGNORECASE,
)
_PERSONAL_SUFFIX_RE = re.compile(r"\s*[-—|·]\s*个人\s*$")


def clean_site_text(text: str) -> str:
    """去掉浏览器多标签/后缀噪音，保留可读文本（不改大小写）。"""
    t = (text or "").strip()
    t = "".join(ch for ch in t if ch not in _ZERO_WIDTH)
    t = _TAB_SUFFIX_RE.sub("", t)
    t = re.sub(r"\s*和另外\s*$", "", t)  # 被截断只剩“和另外”的尾巴
    t = _BROWSER_SUFFIX_RE.sub("", t)
    t = _PERSONAL_SUFFIX_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_site_key(text: str) -> str:
    """站点分组 key：小写、去后缀噪音、URL 只保留主机名、去掉 www.。"""
    t = clean_site_text(text).lower()
    if "://" in t:
        t = t.split("://", 1)[1].split("/", 1)[0]
    elif "/" in t and "." in t.split("/", 1)[0]:
        t = t.split("/", 1)[0]
    t = t.split("?", 1)[0].split("#", 1)[0]
    if t.startswith("www."):
        t = t[4:]
    return t.strip(" .-_")


def fmt_duration(seconds: float) -> str:
    """把秒数格式化为“x小时y分”的中文可读形式。"""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def fmt_hms(seconds: float) -> str:
    """把秒数格式化为 HH:MM:SS。"""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_minsec(seconds: float) -> str:
    """把秒数格式化为“X分钟X秒 / X小时X分钟”。"""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分钟"
    if m:
        return f"{m}分钟{s}秒"
    return f"{s}秒"
