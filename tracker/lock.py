"""跨进程采集互斥锁。

设计要点：

* **原子获取**：用 ``os.open(path, O_CREAT | O_EXCL)`` 一步完成“创建锁文件”，
  由操作系统保证同一时刻只有一个调用者成功，彻底消除旧实现里
  “先 exists() 检查、再 write_text() 写入”之间的竞态窗口。
* **fail-closed**：锁文件无法创建/读取时抛 :class:`LockError`，由调用方放弃
  本次采集，而不是放行。旧的 ``except Exception: return True`` 会在磁盘满、
  权限不足等情况下静默地让两个采集进程同时写库，属于最坏结果。
* **PID 复用防护**：锁文件内容为 ``<pid>|<进程创建时间>``。仅凭 PID 存活
  判断会把“旧进程退出后 PID 被新进程占用”误判成采集仍在运行，因此额外
  比对进程创建时间。
"""
from __future__ import annotations

import os
from pathlib import Path

from .config import project_root


class LockError(RuntimeError):
    """锁文件无法创建/读取/删除；调用方应阻止采集启动（fail-closed）。"""


# GetExitCodeProcess 的“仍在运行”标记（winbase.h 的 STILL_ACTIVE）
_STILL_ACTIVE = 259


def process_created_time(pid: int) -> tuple[bool, int]:
    """返回 ``(进程是否存活, 进程创建时间[微秒, 1601 纪元])``。

    注意：``OpenProcess`` 对**刚退出但内核对象尚未回收**的进程同样会成功，
    只凭它判断存活会把已退出的采集进程误认成“还在跑”，进而让锁永远无法
    被回收。因此这里额外用 ``GetExitCodeProcess`` 确认进程真的仍在运行。

    无法判断时保守返回 ``(True, 0)``，即倾向于认为目标进程仍然存活，
    避免误删别人正在持有的锁。
    """
    try:
        import ctypes
        from ctypes import wintypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False, 0
        try:
            exit_code = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)):
                return False, 0
            if exit_code.value != _STILL_ACTIVE:
                return False, 0

            ct = wintypes.FILETIME()
            et = wintypes.FILETIME()
            kt = wintypes.FILETIME()
            ut = wintypes.FILETIME()
            if ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(ct), ctypes.byref(et),
                    ctypes.byref(kt), ctypes.byref(ut)):
                return True, (ct.dwHighDateTime << 32) | ct.dwLowDateTime
            return True, 0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 探测失败时保守认为存活
        return True, 0


class TrackingLock:
    """跨进程采集互斥锁（``data/tracking.lock`` + PID/创建时间校验）。

    ``start`` 与 ``dashboard`` 共用同一把锁，保证同一时间只有一个
    ``run_tracking`` 采集线程在写数据库，避免时长被重复统计。
    """

    _MAX_ATTEMPTS = 3

    def __init__(self, cfg: dict | None = None, *, path: Path | None = None):
        if path is None:
            cfg = cfg or {}
            path = project_root() / cfg.get("data_dir", "data") / "tracking.lock"
        self.path = Path(path)
        self._held = False

    # ---------- 对外接口 ----------

    def acquire(self) -> bool:
        """尝试获取锁。

        :returns: ``True`` 表示本进程已持有；``False`` 表示已有存活进程持有。
        :raises LockError: 锁文件无法操作（调用方必须放弃采集）。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LockError(f"无法创建锁目录 {self.path.parent}：{exc}") from exc

        payload = self._payload()
        for _ in range(self._MAX_ATTEMPTS):
            try:
                # O_EXCL 由内核保证原子性：同一时刻只有一个进程能创建成功
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._holder_alive():
                    return False
                # 持有者已退出（或锁文件损坏）：清掉陈旧锁后重试
                self._remove_stale()
                continue
            except OSError as exc:
                raise LockError(f"无法创建锁文件 {self.path}：{exc}") from exc

            try:
                os.write(fd, payload.encode("utf-8"))
            except OSError as exc:
                raise LockError(f"无法写入锁文件 {self.path}：{exc}") from exc
            finally:
                os.close(fd)

            self._held = True
            return True

        # 连续抢不到：保守判定为已有采集进程在跑
        return False

    def release(self) -> None:
        """释放锁；仅当锁文件仍属于本进程时才删除。

        :raises LockError: 锁文件存在但无法读取/删除。
        """
        if not self.path.exists():
            self._held = False
            return

        try:
            content = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self._held = False
            return
        except OSError as exc:
            raise LockError(f"无法读取锁文件 {self.path}：{exc}") from exc

        pid_s, _, created_s = content.partition("|")
        if pid_s != str(os.getpid()):
            return  # 别人的锁，不动

        _alive, created = process_created_time(os.getpid())
        if created_s and created_s.isdigit() and created and created != int(created_s):
            return  # PID 被复用，这把锁已经不属于本进程

        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise LockError(f"无法删除锁文件 {self.path}：{exc}") from exc
        self._held = False

    def force_unlock(self) -> bool:
        """无条件清除锁文件（``python main.py unlock`` 用）。

        :returns: 是否确实删除了一个已存在的锁文件。
        :raises LockError: 锁文件存在但无法删除。
        """
        try:
            existed = self.path.exists()
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise LockError(f"无法清除锁文件 {self.path}：{exc}") from exc
        self._held = False
        return existed

    # ---------- 内部实现 ----------

    def _payload(self) -> str:
        _alive, created = process_created_time(os.getpid())
        return f"{os.getpid()}|{created}"

    def _holder_alive(self) -> bool:
        """判断锁文件记录的持有者是否仍存活（内容损坏时保守认为存活）。"""
        try:
            content = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return False  # 已被别人删掉，可以重试抢锁
        except OSError as exc:
            raise LockError(f"无法读取锁文件 {self.path}：{exc}") from exc

        pid_s, _, created_s = content.partition("|")
        if not pid_s.isdigit():
            return False  # 内容损坏，视为陈旧锁

        alive, created = process_created_time(int(pid_s))
        if not alive:
            return False
        if not created_s:
            return True  # 旧格式（无创建时间），只能凭 PID 判断
        if not created_s.isdigit():
            return True  # 内容异常，保守认为存活
        return created == int(created_s)

    def _remove_stale(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise LockError(f"无法清除陈旧锁文件 {self.path}：{exc}") from exc
