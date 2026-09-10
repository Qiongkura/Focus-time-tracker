"""浏览器站点识别：节流不写永久缓存、命中后复用。"""
from __future__ import annotations

import tracker.browser as br
from tracker.browser import SiteInfo


def _reset():
    br._cache.clear()
    br._last_db_query.clear()


def test_throttle_does_not_persist_fallback(monkeypatch):
    _reset()
    # 模拟节流：查询时间戳置为“刚刚”
    import time as _time
    br._last_db_query["chrome.exe"] = _time.monotonic()
    called = {"n": 0}

    def fake_resolve(_title):
        called["n"] += 1
        return SiteInfo(site="real.com", title="Real", url="https://real.com")

    monkeypatch.setattr(br, "_resolve_chromium", fake_resolve)
    first = br.resolve_site("chrome.exe", "Some Page Title")
    # 节流期：标题兜底，且不应写入永久缓存
    assert first.site != "real.com"
    assert ("chrome.exe", "Some Page Title") not in br._cache
    assert called["n"] == 0

    # 节流到期后再次解析应真正查库
    br._last_db_query["chrome.exe"] = 0.0
    second = br.resolve_site("chrome.exe", "Some Page Title")
    assert second.site == "real.com"
    assert br._cache[("chrome.exe", "Some Page Title")].site == "real.com"
    assert called["n"] == 1

    # 命中缓存不再查库
    third = br.resolve_site("chrome.exe", "Some Page Title")
    assert third.site == "real.com"
    assert called["n"] == 1
    _reset()


def test_cache_max_evicts_old(monkeypatch):
    _reset()
    br._CACHE_MAX = 3
    try:
        monkeypatch.setattr(br, "_db_query_due", lambda _p: True)
        monkeypatch.setattr(
            br, "_resolve_chromium",
            lambda title: SiteInfo(site=f"s{len(br._cache)}.example", title=title, url=""),
        )
        for i in range(5):
            br.resolve_site("chrome.exe", f"t{i}")
        assert len(br._cache) <= 3
    finally:
        br._CACHE_MAX = 512
        _reset()
