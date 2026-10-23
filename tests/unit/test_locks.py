import datetime
import json
import os

import pytest

from curator.fs.locks import FolderLock, FolderLocked, is_stale, lock_filename, normalize, read_lock


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


V1_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "v1_lock_sample.json")


def v1_sample():
    """Файл блокировки версии 1 (снят кодом ветки v1; поля — как на Win7, путь — UNC)."""
    with open(V1_SAMPLE, encoding="utf-8") as stream:
        return json.load(stream)


def test_lock_file_format_matches_version_1(tmp_path):
    folder = str(tmp_path / "Объект 1")
    lock = FolderLock(folder, str(tmp_path / "locks")).acquire()
    try:
        written = read_lock(lock.path)
    finally:
        lock.release()
    sample = v1_sample()
    assert sorted(written) == sorted(sample)
    for key in sample:
        assert type(written[key]) is type(sample[key]), key


def test_lock_name_is_sha1_of_normalized_path_as_in_version_1():
    import hashlib

    sample = v1_sample()
    expected = hashlib.sha1(sample["normalized"].encode("utf-8")).hexdigest() + ".lock"
    assert expected == "aca0b0e28e5ebd5238f68274c56b42881ae490d7.lock"
    if os.name == "nt":
        assert normalize(sample["folder"]) == sample["normalized"]
        assert lock_filename(sample["folder"]) == expected


def test_version_1_lock_blocks_and_is_not_removed(tmp_path):
    folder = str(tmp_path / "Объект 1")
    directory = tmp_path / "locks"
    directory.mkdir()
    sample = dict(v1_sample(), folder=folder, normalized=normalize(folder),
                  heartbeat=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
    path = directory / lock_filename(folder)
    path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FolderLocked) as error:
        FolderLock(folder, str(directory)).acquire()
    assert "ivanov" in str(error.value) and "RENTGEN-PC" in str(error.value)
    assert read_lock(str(path)) == sample
