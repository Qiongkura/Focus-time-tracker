"""页面之间共用的纯展示辅助函数。

这些函数不持有任何窗口状态，只做字符串整理，因此放在独立模块里，
避免各页面 mixin 互相 import（那会绕回 `app.py` 造成循环依赖）。
"""
from __future__ import annotations


def shorten(text: str, n: int) -> str:
    """超长文本截断并加省略号，保证宽度 ``n`` 内可读。"""
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def site_display(s) -> str:
    """网站条目的展示名：优先用裸域名，否则回退到标题。

    ``s`` 是会话行（dict 或 sqlite3.Row），需要 ``site`` / ``title`` 字段。
    """
    site = (s.get("site") or "").strip()
    if "." in site and " " not in site and "/" not in site:
        return site
    return s.get("title") or site
