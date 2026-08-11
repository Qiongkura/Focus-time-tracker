"""Windows 前台窗口采集（仅依赖标准库 ctypes）。"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from .browser import resolve_site
from .games import is_game
from .overrides import override_for

if os.name != "nt":
    raise RuntimeError("本工具仅支持 Windows（依赖 Win32 API 获取前台窗口）")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 声明函数签名，避免 64 位指针被截断
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

SPECIAL_CLASSES = {"Progman": "桌面", "WorkerW": "桌面", "LockScreen": "锁屏"}
BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "firefox.exe"}


def _process_name_of(pid: int):
    """根据 PID 返回 (进程名, 完整路径)。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return "未知进程", ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = Path(buf.value)
            return path.name, str(path)
        return "未知进程", ""
    finally:
        kernel32.CloseHandle(handle)


def _collect_window() -> dict | None:
    """Win32 采集原始前台窗口数据；无窗口时返回 None。测试可替换此函数。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
    title = title_buf.value.strip()

    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, 256)
    class_name = class_buf.value

    minimized = bool(user32.IsIconic(hwnd))
    process, exe_path = _process_name_of(pid.value)

    return {
        "hwnd": hwnd,
        "pid": pid.value,
        "process": process,
        "exe_path": exe_path,
        "title": title,
        "class_name": class_name,
        "minimized": minimized,
    }


def classify_window(process: str = "", exe_path: str = "", title: str = "",
                    class_name: str = "", minimized: bool = False,
                    hwnd: int = 0, pid: int = 0) -> dict:
    """纯分类逻辑：系统（桌面/锁屏/最小化） > 手动覆盖 > 游戏（规则引擎） > 网站 > 应用。"""
    # 分类：系统（桌面/锁屏/最小化） > 手动覆盖（应用/游戏） > 游戏（规则引擎） > 网站 > 应用
    category = "应用"
    if class_name in SPECIAL_CLASSES:
        process, exe_path = SPECIAL_CLASSES[class_name], ""
        category = "系统"
    elif not title and class_name == "Windows.UI.Core.CoreWindow":
        process, exe_path = "锁屏", ""
        category = "系统"
    elif minimized:
        process, exe_path = "最小化", ""
        category = "系统"
    elif override_for(process) == "游戏":
        category = "游戏"
    elif override_for(process) == "应用":
        category = "应用"
    elif is_game(exe_path, process, title):
        category = "游戏"
    elif process.lower() in BROWSER_PROCESSES:
        category = "网站"
    elif not process:
        process = "未知进程"

    info = {
        "hwnd": hwnd,
        "pid": pid,
        "process": process,
        "exe_path": exe_path,
        "title": title,
        "class_name": class_name,
        "minimized": minimized,
        "category": category,
        "site": "",
        "site_title": "",
        "url": "",
    }

    if category == "网站":
        site = resolve_site(process.lower(), title)
        info["site"] = site.site
        info["site_title"] = site.title or title
        info["url"] = site.url

    return info


def get_foreground_info():
    """返回当前前台窗口信息（含分类与网站）；无窗口时返回 None。"""
    raw = _collect_window()
    if raw is None:
        return None
    return classify_window(**raw)


def _session_key(info: dict) -> tuple:
    """会话身份：网站按站点切换，其余按 分类+进程 切换。"""
    if info["category"] == "网站":
        return ("网站", info.get("site") or "未知网站")
    return (info["category"], info["process"])


def _close_session(db, current, now, min_session):
    """结束一个会话；时长不足 min_session 则不写入。"""
    if current is None:
        return
    duration = (now - current["start"]).total_seconds()
    if duration >= min_session:
        db.add_session(
            current["start"], now, current["process"], current["exe_path"],
            current["title"], current["category"], current["site"], current["url"],
        )


def run_tracking(db, cfg: dict, stop_event=None) -> None:
    """前台窗口采样主循环，Ctrl+C 或 stop_event 优雅退出。"""
    interval = float(cfg.get("poll_interval_seconds", 1.0))
    min_session = float(cfg.get("min_session_seconds", 3))
    exclude = set(cfg.get("exclude_processes", []) or [])
    browser_site = bool(cfg.get("browser_site_tracking", True))

    current = None
    print("开始记录前台窗口使用时长（按 Ctrl+C 停止）...")
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            info = get_foreground_info()
            now = datetime.now()

            if info is not None and not browser_site and info["category"] == "网站":
                # 关闭网站细分后，浏览器按普通应用统计
                info = dict(info)
                info["category"] = "应用"
                info["site"] = ""
                info["url"] = ""

            if info is None or info["process"] in exclude:
                _close_session(db, current, now, min_session)
                current = None
            else:
                key = _session_key(info)
                if current is not None and current["key"] == key:
                    pass  # 同一会话继续累计
                else:
                    _close_session(db, current, now, min_session)
                    current = {
                        "key": key,
                        "process": info["process"],
                        "exe_path": info["exe_path"],
                        "title": info.get("site_title") or info["title"],
                        "category": info["category"],
                        "site": info.get("site", ""),
                        "url": info.get("url", ""),
                        "start": now,
                    }
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n正在保存最后一段会话并退出...")
    finally:
        _close_session(db, current, datetime.now(), min_session)
