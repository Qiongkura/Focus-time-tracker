"""采集锁：原子获取、fail-closed、PID 复用防护。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tracker.lock import LockError, TrackingLock

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "tracking.lock"


def _write_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------- 基本获取/释放 ----------

def test_acquire_creates_lock_file(lock_path):
    lock = TrackingLock(path=lock_path)
    assert lock.acquire() is True
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8").split("|")[0] == str(os.getpid())


def test_acquire_creates_missing_parent_dir(tmp_path):
    lock = TrackingLock(path=tmp_path / "a" / "b" / "tracking.lock")
    assert lock.acquire() is True
    assert lock.path.exists()


def test_second_acquire_fails_while_held(lock_path):
    first = TrackingLock(path=lock_path)
    assert first.acquire() is True
    # 同一进程重复获取也必须失败，避免自我重入导致双重采集
    assert TrackingLock(path=lock_path).acquire() is False


def test_release_removes_own_lock(lock_path):
    lock = TrackingLock(path=lock_path)
    lock.acquire()
    lock.release()
    assert not lock_path.exists()


def test_release_is_idempotent(lock_path):
    lock = TrackingLock(path=lock_path)
    lock.release()          # 没有锁时释放不应报错
    lock.acquire()
    lock.release()
    lock.release()
    assert not lock_path.exists()


def test_release_keeps_foreign_lock(lock_path):
    _write_lock(lock_path, "424242|0")
    TrackingLock(path=lock_path).release()
    # 不能误删别人的锁
    assert lock_path.exists()


# ---------- 陈旧锁与损坏锁 ----------

def test_stale_lock_is_taken_over(lock_path):
    _write_lock(lock_path, "999999999|0")      # 不存在的 PID
    lock = TrackingLock(path=lock_path)
    assert lock.acquire() is True
    assert lock_path.read_text(encoding="utf-8").split("|")[0] == str(os.getpid())


def test_lock_without_created_time_is_taken_over(lock_path):
    _write_lock(lock_path, "999999999")        # 旧格式，PID 不存在
    assert TrackingLock(path=lock_path).acquire() is True


def test_corrupted_lock_is_treated_as_stale(lock_path):
    _write_lock(lock_path, "garbage-not-a-pid")
    assert TrackingLock(path=lock_path).acquire() is True


def test_foreign_live_pid_blocks_acquire(lock_path):
    # 当前进程自身就是一个“存活进程”，用它的 PID 冒充持有者
    _write_lock(lock_path, f"{os.getpid()}|")
    assert TrackingLock(path=lock_path).acquire() is False


# ---------- fail-closed ----------

def test_acquire_fails_closed_when_lock_path_is_a_directory(tmp_path):
    blocked = tmp_path / "tracking.lock"
    blocked.mkdir()
    with pytest.raises(LockError):
        TrackingLock(path=blocked).acquire()


def test_acquire_fails_closed_when_parent_is_a_file(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(LockError):
        TrackingLock(path=blocker / "tracking.lock").acquire()


# ---------- force_unlock ----------

def test_force_unlock_removes_lock(lock_path):
    lock = TrackingLock(path=lock_path)
    lock.acquire()
    assert lock.force_unlock() is True
    assert not lock_path.exists()
    assert lock.force_unlock() is False      # 已经没有锁了


# ---------- 真实跨进程互斥 ----------

def test_live_foreign_process_blocks_acquire(lock_path):
    """另一个真实存活的进程持有锁时，本进程必须拿不到。"""
    code = (
        "import sys, time;"
        f"sys.path.insert(0, r'{PROJECT_ROOT}');"
        "from pathlib import Path;"
        "from tracker.lock import TrackingLock;"
        f"lock = TrackingLock(path=Path(r'{lock_path}'));"
        "print(lock.acquire(), flush=True);"
        "time.sleep(10)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "True"
        assert lock_path.exists()
        assert TrackingLock(path=lock_path).acquire() is False
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_dead_process_lock_is_reclaimed(lock_path):
    """持有者进程已退出时，锁必须能被重新获取（不能永久卡死）。"""
    code = (
        "import sys;"
        f"sys.path.insert(0, r'{PROJECT_ROOT}');"
        "from pathlib import Path;"
        "from tracker.lock import TrackingLock;"
        f"lock = TrackingLock(path=Path(r'{lock_path}'));"
        "print(lock.acquire(), flush=True)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=30)
    assert out.strip() == "True"
    assert lock_path.exists()                 # 进程退出但没 release，锁文件残留

    assert TrackingLock(path=lock_path).acquire() is True


# ---------- holder_alive（GUI 只读查询） ----------

def test_holder_alive_false_when_no_lock_file(lock_path):
    assert TrackingLock(path=lock_path).holder_alive() is False


def test_holder_alive_true_for_live_holder(lock_path):
    lock = TrackingLock(path=lock_path)
    lock.acquire()
    assert lock.holder_alive() is True


def test_holder_alive_false_for_dead_pid(lock_path):
    _write_lock(lock_path, "999999999|0")
    assert TrackingLock(path=lock_path).holder_alive() is False


def test_holder_alive_false_for_corrupted_content(lock_path):
    _write_lock(lock_path, "garbage-not-a-pid")
    assert TrackingLock(path=lock_path).holder_alive() is False


def test_holder_alive_does_not_modify_lock_file(lock_path):
    """只读查询不能顺手把锁删掉，否则会把正在跑的采集进程放进来。"""
    lock = TrackingLock(path=lock_path)
    lock.acquire()
    before = lock_path.read_text(encoding="utf-8")
    lock.holder_alive()
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == before
