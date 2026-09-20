"""配置加载与校验。

对外只暴露 :func:`load_config` / :func:`load_config_detailed`，把用户手改的
``config.json`` 规整成一份可信的配置字典。这样做的原因是：一个写错的字段
足以让整个采集链路出问题，而且症状往往很难定位——

* ``poll_interval_seconds: 0`` 会让采集线程变成忙循环，CPU 直接打满；
* ``exclude_processes: "python.exe"``（字符串而非列表）会被当成字符集合，
  逐字符比较，排除规则静默失效；
* ``exclude_processes: ["Chrome.exe"]`` 因为 Windows 返回的是 ``chrome.exe``，
  大小写不匹配同样导致排除失效；
* JSON 语法错误会让 GUI 在启动阶段直接抛异常。

校验策略是「就地修正 + 报告问题」，而不是拒绝启动：宁可带着一份安全的配置
继续跑，也不让用户面对一个打不开的程序。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

DEFAULTS = {
    "poll_interval_seconds": 1.0,   # 前台窗口采样间隔（秒）
    "min_session_seconds": 3,       # 低于该时长的小片段不记录
    "checkpoint_seconds": 45.0,     # 长会话每隔该时长落盘一段，防强杀/断电丢数据
    "exclude_processes": [],        # 不想统计的进程名列表，如 ["explorer.exe"]
    "browser_site_tracking": True,  # 识别浏览器当前访问的具体网站
    "data_dir": "data",             # SQLite 数据目录
    "report_dir": "reports",        # 图表输出目录
    # ---- 隐私与数据保留 ----
    "retention_days": 0,            # 历史保留天数，0 = 永久保留
    "store_full_url": True,         # False 时 URL 只保存「协议://域名」
    "strip_url_query": True,        # 保存 URL 时去掉 ?query 与 #fragment
}

# 采样间隔下限：低于此值会让采集线程变成高频轮询，白烧 CPU
MIN_POLL_INTERVAL = 0.2
# checkpoint 下限：更短的落盘间隔只会让数据库写放大
MIN_CHECKPOINT_SECONDS = 1.0

def _defaults() -> dict:
    """默认配置的深拷贝：避免调用方改到 DEFAULTS 里共享的可变对象。"""
    return {**DEFAULTS, "exclude_processes": list(DEFAULTS["exclude_processes"])}


_TRUTHY = ("true", "1", "yes", "on")
_FALSY = ("false", "0", "no", "off")


def _as_float(raw, default: float) -> tuple[float, bool]:
    """解析数值，返回 ``(值, 是否解析成功)``；布尔值不算有效数字。"""
    if isinstance(raw, bool):
        return default, False
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, False
    if math.isnan(value) or math.isinf(value):
        return default, False
    return value, True


def _as_bool(raw, default: bool) -> tuple[bool, bool]:
    """解析布尔值，返回 ``(值, 是否解析成功)``。"""
    if isinstance(raw, bool):
        return raw, True
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw), True
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in _TRUTHY:
            return True, True
        if text in _FALSY:
            return False, True
    return default, False


def _as_process_list(raw) -> list[str]:
    """进程名列表：接受 list/tuple/set 或单个字符串，统一小写、去空白、去重。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip().lower()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _as_path_text(raw, default: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return default
    return raw.strip()


def normalize_config(raw) -> tuple[dict, list[str]]:
    """把任意来源的配置规整成可信配置。

    :returns: ``(cfg, problems)``，``problems`` 列出发现并已修正的问题。
    """
    if not isinstance(raw, dict):
        return _defaults(), ["配置根节点不是 JSON 对象，已全部回退默认值"]

    cfg = _defaults()
    problems: list[str] = []

    unknown = sorted(set(raw) - set(DEFAULTS))
    if unknown:
        problems.append("忽略未知配置项：" + "、".join(unknown))

    # ---- 数值项：类型错误回退默认值，越界夹到安全边界 ----
    numeric = (
        ("poll_interval_seconds", MIN_POLL_INTERVAL, None),
        ("min_session_seconds", 0.0, None),
        ("checkpoint_seconds", MIN_CHECKPOINT_SECONDS, None),
    )
    for key, low, high in numeric:
        if key not in raw:
            continue
        default = float(DEFAULTS[key])
        value, ok = _as_float(raw[key], default)
        if not ok:
            problems.append(f"{key}={raw[key]!r} 不是有效数字，已回退默认值 {default:g}")
            cfg[key] = default
            continue
        clamped = value
        if low is not None:
            clamped = max(clamped, low)
        if high is not None:
            clamped = min(clamped, high)
        if clamped != value:
            problems.append(
                f"{key}={value:g} 超出允许范围 [{low:g}, {high if high is not None else '∞'}]，"
                f"已调整为 {clamped:g}"
            )
        cfg[key] = clamped

    # checkpoint 必须不短于最小会话时长，否则每次 checkpoint 落盘都会被丢弃
    if cfg["checkpoint_seconds"] < cfg["min_session_seconds"]:
        problems.append(
            f"checkpoint_seconds={cfg['checkpoint_seconds']:g} 小于 "
            f"min_session_seconds={cfg['min_session_seconds']:g}，"
            f"已提升到 {cfg['min_session_seconds']:g}"
        )
        cfg["checkpoint_seconds"] = cfg["min_session_seconds"]

    # ---- 布尔项 ----
    for key in ("browser_site_tracking", "store_full_url", "strip_url_query"):
        if key not in raw:
            continue
        value, ok = _as_bool(raw[key], DEFAULTS[key])
        if not ok:
            problems.append(
                f"{key}={raw[key]!r} 不是布尔值，已回退默认值 {DEFAULTS[key]}"
            )
        cfg[key] = value

    # ---- 历史保留天数（非负整数，0 = 永久保留） ----
    if "retention_days" in raw:
        value, ok = _as_float(raw["retention_days"], 0.0)
        if not ok:
            problems.append(
                f"retention_days={raw['retention_days']!r} 不是有效数字，"
                f"已回退 0（永久保留）"
            )
            cfg["retention_days"] = 0
        else:
            days = max(0, int(value))
            if days != value:
                problems.append(f"retention_days={value:g} 已取整为 {days} 天")
            cfg["retention_days"] = days

    # ---- 进程排除列表 ----
    if "exclude_processes" in raw:
        raw_exclude = raw["exclude_processes"]
        normalized = _as_process_list(raw_exclude)
        if not isinstance(raw_exclude, (list, tuple, set)):
            problems.append(
                f"exclude_processes 应为列表，实际是 {type(raw_exclude).__name__}，"
                f"已规整为 {normalized!r}"
            )
        elif normalized != [str(x).strip().lower() for x in raw_exclude]:
            problems.append(f"exclude_processes 已统一为小写去空白：{normalized!r}")
        cfg["exclude_processes"] = normalized

    # ---- 路径项 ----
    for key in ("data_dir", "report_dir"):
        if key not in raw:
            continue
        value = _as_path_text(raw[key], DEFAULTS[key])
        if value == DEFAULTS[key] and not (
                isinstance(raw[key], str) and raw[key].strip() == DEFAULTS[key]):
            problems.append(
                f"{key}={raw[key]!r} 不是有效路径，已回退默认值 {DEFAULTS[key]!r}"
            )
        cfg[key] = value

    return cfg, problems


def _report(logger, message: str) -> None:
    """把配置问题写到调用方提供的 logger（GUI 用），否则退到 stderr。"""
    if logger is not None:
        try:
            logger(message)
            return
        except Exception:  # noqa: BLE001 - 日志失败不能影响配置加载
            pass
    print(f"【配置】{message}", file=sys.stderr)


def load_config_detailed(path: Path, *, logger=None) -> tuple[dict, list[str]]:
    """读取并校验配置，同时返回发现的问题列表（GUI 可用来提示用户）。"""
    path = Path(path)
    if not path.exists():
        return _defaults(), []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        message = f"配置文件 {path} 读取失败（{exc}），本次使用默认配置"
        _report(logger, message)
        return _defaults(), [message]

    cfg, problems = normalize_config(raw)
    for problem in problems:
        _report(logger, f"{path.name}: {problem}")
    return cfg, problems


def load_config(path: Path, *, logger=None) -> dict:
    """读取并校验配置；任何异常都退化为默认配置，不向上抛。"""
    cfg, _ = load_config_detailed(path, logger=logger)
    return cfg


def project_root() -> Path:
    """项目根目录：源码运行时为 main.py 所在目录；打包成 exe 后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
