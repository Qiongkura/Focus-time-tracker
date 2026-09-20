"""Windows 前台窗口采集（仅依赖标准库 ctypes）。

内部按职责拆成几个可单独测试的组件：

* :class:`WindowSampler`      —— 取前台窗口信息（采集函数可注入，便于测试）
* :class:`SessionAccumulator` —— 维护「当前会话」，处理切段与 checkpoint
* :class:`SessionWriter`      —— 把会话段写进数据库
* :class:`TrackingService`    —— 编排上面几者的主循环

模块级仍保留 ``get_foreground_info`` / ``classify_window`` / ``run_tracking``
等原有函数，行为不变，外部调用方（main.py、app.py、测试）无需改动。
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from .browser import resolve_site
from .config import MIN_POLL_INTERVAL
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


def _session_key(info: dict) -> tuple:
    """会话身份：网站按站点切换，其余按 分类+进程 切换。"""
    if info["category"] == "网站":
        return ("网站", info.get("site") or "未知网站")
    return (info["category"], info["process"])


# ---------- 组件 ----------

class WindowSampler:
    """取前台窗口信息。

    ``collect`` 可注入，方便测试替换掉真实的 Win32 调用。
    """

    def __init__(self, collect=None):
        self._collect = collect or _collect_window

    def sample(self) -> dict | None:
        """返回原始窗口数据；无前台窗口时返回 None。"""
        return self._collect()

    def info(self) -> dict | None:
        """返回已分类的窗口信息；无前台窗口时返回 None。"""
        raw = self.sample()
        if raw is None:
            return None
        return classify_window(**raw)


_default_sampler = WindowSampler()


class SessionWriter:
    """把会话段写进数据库；时长不足 ``min_session`` 的段直接丢弃。"""

    def __init__(self, db, min_session: float = 3.0):
        self.db = db
        self.min_session = max(0.0, float(min_session))

    def write(self, session: dict | None, end: datetime) -> bool:
        """写一段会话；返回是否真的落库。"""
        if session is None:
            return False
        duration = (end - session["start"]).total_seconds()
        if duration < self.min_session:
            return False
        self.db.add_session(
            session["start"], end, session["process"], session["exe_path"],
            session["title"], session["category"], session["site"], session["url"],
        )
        return True


class SessionAccumulator:
    """维护「当前会话」，决定切段与长会话 checkpoint。

    它不直接写库，只交出「该写哪一段、写到什么时刻」，落库交给
    :class:`SessionWriter`——这样切段逻辑可以脱离数据库单独测试。
    """

    def __init__(self, min_session: float = 3.0, checkpoint_seconds: float = 45.0):
        self.min_session = max(0.0, float(min_session))
        self.checkpoint_seconds = float(checkpoint_seconds)
        self.current: dict | None = None
        self.last_checkpoint: datetime | None = None

    def observe(self, info: dict | None, now: datetime) -> list[tuple[dict, datetime]]:
        """喂入一次采样，返回本次需要落库的 ``(session, end_time)`` 列表。"""
        pending: list[tuple[dict, datetime]] = []

        if info is None:
            segment = self._close(now)
            if segment:
                pending.append(segment)
            return pending

        key = _session_key(info)
        if self.current is not None and self.current["key"] == key:
            # 长会话 checkpoint：先落已写过的段，再从 now 重新起算 start，
            # 进程被强杀时最多丢 checkpoint_seconds 内的时长
            if (self.last_checkpoint is not None
                    and (now - self.last_checkpoint).total_seconds() >= self.checkpoint_seconds):
                segment = self._segment(self.current, now, self.checkpoint_seconds * 0.5)
                if segment:
                    pending.append(segment)
                self.current = dict(self.current)
                self.current["start"] = now
                self.last_checkpoint = now
            return pending

        segment = self._close(now)
        if segment:
            pending.append(segment)
        self.current = {
            "key": key,
            "process": info["process"],
            "exe_path": info["exe_path"],
            "title": info.get("site_title") or info["title"],
            "category": info["category"],
            "site": info.get("site", ""),
            "url": info.get("url", ""),
            "start": now,
        }
        self.last_checkpoint = now
        return pending

    def flush(self, now: datetime) -> tuple[dict, datetime] | None:
        """退出时交出最后一段（没有进行中的会话则返回 None）。"""
        return self._close(now)

    def _close(self, now: datetime) -> tuple[dict, datetime] | None:
        if self.current is None:
            return None
        segment = (self.current, now)
        self.current = None
        self.last_checkpoint = None
        return segment

    @staticmethod
    def _segment(session: dict, end: datetime,
                 min_duration: float) -> tuple[dict, datetime] | None:
        if (end - session["start"]).total_seconds() < min_duration:
            return None
        return (session, end)


class TrackingService:
    """编排「采样 → 分类 → 累积 → 落库」的主循环。"""

    def __init__(self, db, cfg: dict, stop_event=None, fetch_info=None):
        # 下限保护：本类也可能被直接构造，此时 cfg 未必经过 config.normalize_config
        self.interval = max(
            MIN_POLL_INTERVAL, float(cfg.get("poll_interval_seconds", 1.0) or 1.0))
        min_session = max(0.0, float(cfg.get("min_session_seconds", 3) or 0))
        self.stop_event = stop_event
        # fetch_info 可注入；默认走模块级 get_foreground_info，测试常替换它
        self.fetch_info = fetch_info or get_foreground_info
        self.browser_site_tracking = bool(cfg.get("browser_site_tracking", True))
        self.accumulator = SessionAccumulator(
            min_session=min_session,
            checkpoint_seconds=float(cfg.get("checkpoint_seconds", 45.0) or 45.0),
        )
        self.writer = SessionWriter(db, min_session=min_session)
        # 进程名统一小写比较：Windows 返回 chrome.exe，而用户习惯按 Chrome.exe 配置
        self.exclude = {str(name).strip().lower()
                        for name in (cfg.get("exclude_processes") or [])}

    def run(self) -> None:
        """主循环；Ctrl+C 或 stop_event 触发时优雅退出。"""
        print("开始记录前台窗口使用时长（按 Ctrl+C 停止）...")
        try:
            while not self._stopped():
                self.tick()
                if self._wait():
                    break
        except KeyboardInterrupt:
            print("\n正在保存最后一段会话并退出...")
        finally:
            self._flush()

    def tick(self) -> None:
        """采样一次，并落库本次需要结束的会话段。"""
        info = self.fetch_info()

        if info is not None and not self.browser_site_tracking and info["category"] == "网站":
            # 关闭网站细分后，浏览器按普通应用统计
            info = dict(info)
            info["category"] = "应用"
            info["site"] = ""
            info["site_title"] = ""
            info["url"] = ""

        if info is not None and (info["process"] or "").strip().lower() in self.exclude:
            info = None

        now = datetime.now()
        for session, end in self.accumulator.observe(info, now):
            self.writer.write(session, end)

    def _stopped(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    def _wait(self) -> bool:
        """等待一个采样间隔；返回 True 表示收到停止信号。"""
        if self.stop_event is not None:
            # 用 wait 代替 sleep：收到停止信号时立即返回，不必等满一个间隔
            return bool(self.stop_event.wait(self.interval))
        time.sleep(self.interval)
        return False

    def _flush(self) -> None:
        segment = self.accumulator.flush(datetime.now())
        if segment:
            self.writer.write(*segment)


# ---------- 兼容层（保持原有模块级接口） ----------

def get_foreground_info():
    """返回当前前台窗口信息（含分类与网站）；无窗口时返回 None。"""
    return _default_sampler.info()


def _write_segment(db, current, now: datetime, min_session: float) -> None:
    """从 current['start'] 写到 now；不足 min_session 则不写。"""
    SessionWriter(db, min_session=min_session).write(current, now)


def _close_session(db, current, now, min_session):
    """结束一个会话；时长不足 min_session 则不写入。"""
    _write_segment(db, current, now, min_session)


def run_tracking(db, cfg: dict, stop_event=None) -> None:
    """前台窗口采样主循环，Ctrl+C 或 stop_event 优雅退出。

    长会话每隔 checkpoint_seconds 落盘一段，进程被强杀时最多丢一段间隔内的时长。
    """
    TrackingService(db, cfg, stop_event).run()
