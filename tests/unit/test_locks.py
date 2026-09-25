import datetime
import json
import os

import pytest

from tifjpg.fs.locks import FolderLock, FolderLocked, is_stale, lock_filename, normalize, read_lock


def test_same_folder_gives_same_lock_name(tmp_path):
    folder = str(tmp_path / "Объект 1")
    assert lock_filename(folder) == lock_filename(folder + os.sep)
    assert lock_filename(folder) != lock_filename(str(tmp_path / "Объект 2"))


def test_normalize_is_case_insensitive(tmp_path):
    folder = str(tmp_path / "Объект 1")
    assert normalize(folder) == normalize(folder.upper()) or os.path.normcase("A") == "A"


def test_second_session_cannot_take_the_same_folder(tmp_path):
    folder = str(tmp_path / "Объект 1")
    directory = str(tmp_path / "locks")
    first = FolderLock(folder, directory).acquire()
    try:
        with pytest.raises(FolderLocked) as error:
            FolderLock(folder, directory).acquire()
        assert "уже обрабатывается" in str(error.value)
        assert os.getpid() == error.value.owner["pid"]
    finally:
        first.release()

    FolderLock(folder, directory).acquire().release()  # после освобождения снова можно


def test_other_folders_are_not_blocked(tmp_path):
    directory = str(tmp_path / "locks")
    first = FolderLock(str(tmp_path / "Объект 1"), directory).acquire()
    second = FolderLock(str(tmp_path / "Объект 2"), directory).acquire()
    first.release()
    second.release()


def test_lock_stores_owner_information(tmp_path):
    folder = str(tmp_path / "Объект 1")
    directory = str(tmp_path / "locks")
    with FolderLock(folder, directory) as lock:
        owner = read_lock(lock.path)
        assert owner["pid"] == os.getpid()
        assert owner["folder"] == folder
        assert owner["host"] and owner["user"]


def test_stale_lock_can_be_taken_over(tmp_path):
    folder = str(tmp_path / "Объект 1")
    directory = str(tmp_path / "locks")
    lock = FolderLock(folder, directory).acquire()
    old = datetime.datetime.now().astimezone() - datetime.timedelta(hours=2)
    owner = read_lock(lock.path)
    owner["heartbeat"] = owner["started"] = old.isoformat(timespec="seconds")
    owner["pid"] = 999999
    with open(lock.path, "w", encoding="utf-8") as stream:
        json.dump(owner, stream)

    with pytest.raises(FolderLocked):
        FolderLock(folder, directory).acquire()

    taken = FolderLock(folder, directory).acquire(take_over_stale=True)
    assert read_lock(taken.path)["pid"] == os.getpid()
    taken.release()


def test_fresh_lock_is_not_stale():
    now = datetime.datetime.now().astimezone()
    assert not is_stale({"heartbeat": now.isoformat(timespec="seconds")})
    assert is_stale({"heartbeat": (now - datetime.timedelta(minutes=30)).isoformat(timespec="seconds")})
    assert is_stale({})
    assert is_stale({"heartbeat": "не дата"})


def test_release_does_not_touch_someone_elses_lock(tmp_path):
    folder = str(tmp_path / "Объект 1")
    directory = str(tmp_path / "locks")
    lock = FolderLock(folder, directory).acquire()
    owner = read_lock(lock.path)
    owner["pid"] = 123456
    owner["host"] = "другой-компьютер"
    with open(lock.path, "w", encoding="utf-8") as stream:
        json.dump(owner, stream)

    lock.release()
    assert os.path.exists(lock.path)
