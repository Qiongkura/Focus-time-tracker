"""页面之间共用的纯展示辅助函数。

这些函数不持有任何窗口状态，只做字符串整理，因此放在独立模块里，
避免各页面 mixin 互相 import（那会绕回 `app.py` 造成循环依赖）。
"""
from __future__ import annotations

import tkinter as tk

from .. import theme


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


def wrap_to_area(label: tk.Label, area, reserve: int = 16) -> None:
    """让 ``label`` 按滚动容器的**画布**宽度自动换行。

    没有 ``wraplength`` 的 ``Label`` 会按整段文本申请宽度：设置页里的数据库
    路径、前台窗口标题长度不可控，一旦变长就会把页面内容撑得比画布还宽，
    缩到最小窗口时右侧只能靠横向滚动才看得到。

    宽度参照物必须是**画布**而不是父容器：内容宽度 = ``max(画布, 内容请求
    宽度, min_width)``，如果按父容器宽度换行，标签会一直申请「容器宽 - 余量」，
    把容器继续撑大，形成永远收敛不了的反馈环。
    """
    def _sync(_event=None):
        try:
            width = max(theme.scale(120),
                        area.canvas.winfo_width() - theme.scale(reserve))
            if int(str(label.cget("wraplength"))) != width:
                label.configure(wraplength=width)
        except (tk.TclError, ValueError):
            pass

    try:
        area.canvas.bind("<Configure>", _sync, add="+")
    except tk.TclError:
        return
    _sync()
