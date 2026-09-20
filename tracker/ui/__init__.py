"""GUI 页面组件包。

``tracker/app.py`` 原本是一个 1800 行左右的单体窗口类，这里按"页面"把
相对独立的部分拆成 mixin，由 ``ScreenTimeApp`` 继承组装。每个 mixin
只通过 ``self`` 访问宿主窗口的公共属性（``root`` / ``db`` / ``pages`` /
``current_page`` 等），因此拆分不改变任何对外调用点。
"""
from .common import shorten, site_display
from .records_page import RecordsPageMixin
from .stats_page import StatsPageMixin

__all__ = [
    "RecordsPageMixin",
    "StatsPageMixin",
    "shorten",
    "site_display",
]
