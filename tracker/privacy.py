"""隐私相关的落库策略：URL 清洗与历史数据保留。

窗口标题和 URL 是这类工具里最敏感的两类字段——URL 的 query 里经常出现
搜索词、文档名、临时 token；标题则可能直接暴露正在处理的项目名。把
「写库时怎么处理 URL」和「历史保留多久」集中到一处，方便统一审查与测试。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit


class UrlPolicy:
    """URL 落库策略。

    :param store_full_url: 为 ``False`` 时只保存 ``协议://域名``，丢弃路径
    :param strip_query: 为 ``True`` 时去掉 ``?query`` 与 ``#fragment``
    """

    __slots__ = ("store_full_url", "strip_query")

    def __init__(self, store_full_url: bool = True, strip_query: bool = True):
        self.store_full_url = bool(store_full_url)
        self.strip_query = bool(strip_query)

    @classmethod
    def from_config(cls, cfg: dict | None) -> "UrlPolicy":
        cfg = cfg or {}
        return cls(
            store_full_url=bool(cfg.get("store_full_url", True)),
            strip_query=bool(cfg.get("strip_url_query", True)),
        )

    def apply(self, url: str) -> str:
        """按策略清洗 URL；清洗后没有可保留的内容时返回空串。"""
        url = (url or "").strip()
        if not url:
            return ""
        if self.store_full_url and not self.strip_query:
            return url

        try:
            parts = urlsplit(url)
        except ValueError:
            return url if self.store_full_url else ""

        if not parts.scheme or not parts.netloc:
            # 不是标准 URL（可能是标题兜底），按 store_full_url 决定去留
            return url if self.store_full_url else ""

        if not self.store_full_url:
            return f"{parts.scheme}://{parts.netloc}"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def apply_retention(db, retention_days: int) -> int:
    """按保留天数清理历史记录，返回删除行数。

    ``retention_days <= 0`` 表示永久保留（默认值），此时不做任何操作。
    清理后顺带 VACUUM 回收空间——否则删掉的数据页仍占着文件体积。
    """
    try:
        days = int(retention_days)
    except (TypeError, ValueError):
        return 0
    if days <= 0:
        return 0

    cutoff = datetime.now() - timedelta(days=days)
    removed = db.purge_sessions_before(cutoff)
    if removed:
        db.vacuum()
    return removed
