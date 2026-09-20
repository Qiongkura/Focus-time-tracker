"""GUI 页面组件包。

``tracker/app.py`` 原本是一个 1800 行左右的单体窗口类，这里按职责把
它拆成若干 mixin，由 ``ScreenTimeApp`` 继承组装。每个 mixin 只通过
``self`` 访问宿主窗口的公共属性（``root`` / ``db`` / ``pages`` /
``current_page`` 等），因此拆分不改变任何对外调用点。

模块一览：

* :mod:`~tracker.ui.app_shell`          窗口骨架与生命周期
* :mod:`~tracker.ui.home_page`          首页
* :mod:`~tracker.ui.stats_page`         统计页
* :mod:`~tracker.ui.records_page`       详细记录页
* :mod:`~tracker.ui.categories_page`    分类页
* :mod:`~tracker.ui.settings_page`      设置页
* :mod:`~tracker.ui.refresh_controller` 刷新调度
* :mod:`~tracker.ui.background`         后台采集进程与托盘
* :mod:`~tracker.ui.preview`            前台窗口预览线程
* :mod:`~tracker.ui.common`             跨页面共用的纯展示函数
"""
from .app_shell import ShellMixin
from .background import BackgroundMixin
from .categories_page import CategoriesPageMixin
from .common import shorten, site_display
from .home_page import HomePageMixin
from .preview import ForegroundPreviewWorker
from .records_page import RecordsPageMixin
from .refresh_controller import RefreshMixin
from .settings_page import SettingsPageMixin
from .stats_page import StatsPageMixin

__all__ = [
    "BackgroundMixin",
    "CategoriesPageMixin",
    "ForegroundPreviewWorker",
    "HomePageMixin",
    "RecordsPageMixin",
    "RefreshMixin",
    "SettingsPageMixin",
    "ShellMixin",
    "StatsPageMixin",
    "shorten",
    "site_display",
]
