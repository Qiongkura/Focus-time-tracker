"""界面错误日志的轮转：避免 ui_errors.log 长期运行后无限增长。"""
from __future__ import annotations

from tracker.app import rotate_log


def test_no_rotation_below_threshold(tmp_path):
    path = tmp_path / "ui_errors.log"
    path.write_text("x" * 100, encoding="utf-8")

    rotate_log(path, max_bytes=1000, keep=3)

    assert path.read_text(encoding="utf-8") == "x" * 100
    assert not (tmp_path / "ui_errors.log.1").exists()


def test_rotation_moves_current_to_one(tmp_path):
    path = tmp_path / "ui_errors.log"
    path.write_text("x" * 2000, encoding="utf-8")

    rotate_log(path, max_bytes=1000, keep=3)

    assert not path.exists()
    assert (tmp_path / "ui_errors.log.1").read_text(encoding="utf-8") == "x" * 2000


def test_rotation_shifts_older_files(tmp_path):
    path = tmp_path / "ui_errors.log"
    path.write_text("n" * 1500, encoding="utf-8")
    (tmp_path / "ui_errors.log.1").write_text("old1", encoding="utf-8")
    (tmp_path / "ui_errors.log.2").write_text("old2", encoding="utf-8")

    rotate_log(path, max_bytes=1000, keep=3)

    assert (tmp_path / "ui_errors.log.1").read_text(encoding="utf-8") == "n" * 1500
    assert (tmp_path / "ui_errors.log.2").read_text(encoding="utf-8") == "old1"
    assert (tmp_path / "ui_errors.log.3").read_text(encoding="utf-8") == "old2"


def test_oldest_file_beyond_keep_is_dropped(tmp_path):
    path = tmp_path / "ui_errors.log"
    path.write_text("n" * 2000, encoding="utf-8")
    (tmp_path / "ui_errors.log.1").write_text("a", encoding="utf-8")
    (tmp_path / "ui_errors.log.2").write_text("b", encoding="utf-8")
    (tmp_path / "ui_errors.log.3").write_text("c", encoding="utf-8")

    rotate_log(path, max_bytes=1000, keep=3)

    assert (tmp_path / "ui_errors.log.1").read_text(encoding="utf-8") == "n" * 2000
    assert (tmp_path / "ui_errors.log.2").read_text(encoding="utf-8") == "a"
    assert (tmp_path / "ui_errors.log.3").read_text(encoding="utf-8") == "b"
    assert not (tmp_path / "ui_errors.log.4").exists()


def test_missing_file_is_noop(tmp_path):
    rotate_log(tmp_path / "nope.log", max_bytes=1, keep=3)
