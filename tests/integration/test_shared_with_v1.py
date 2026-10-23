"""Версии 1 и 2 на одном сетевом диске (ARCHITECTURE.md, 26.4, решение D23).

Раскладка как на сервере: `Программы\\Curator\\` — версия 1, `Программы\\Curator v2\\` — версия 2,
в config.json версии 2 пути к locks и journal версии 1 — относительные, как в config.example.json.
"""

import os

import pytest

from tests.integration.test_executor import listing
from tests.integration.test_service import STANDALONE, _fake_rollback, tree_with_two_folders
from curator.app import events as ev
from curator.app.service import CONTINUE, ProcessingService
from curator.appdirs import AppPaths
from curator.config import Settings
from curator.domain.errors import PreconditionError
from curator.fs.locks import FolderLock, FolderLocked, normalize
from curator.transaction import executor as executor_module

pytestmark = pytest.mark.vips

V1_LOCKS = os.path.join("..", "Curator", "locks")
V1_JOURNAL = os.path.join("..", "Curator", "journal")


def version_1(tmp_path):
    paths = AppPaths(str(tmp_path / "Программы" / "Curator"))
    return ProcessingService(settings=Settings(network_retry_delays_s=(0,), **STANDALONE), paths=paths)


def version_2(tmp_path, on_event=None, **settings):
    options = dict(locks_dir=V1_LOCKS, legacy_journal_dirs=(V1_JOURNAL,), network_retry_delays_s=(0,))
    options.update(settings)
    paths = AppPaths(str(tmp_path / "Программы" / "Curator v2"))
    return ProcessingService(settings=Settings(**options), paths=paths, on_event=on_event)


def crash_version_1_in_phase_b(tmp_path, monkeypatch, root, folder):
    """Версия 1 обрывается посреди фиксации и не закрывает журнал — так выглядит сбой питания."""
    v1 = version_1(tmp_path)
    original = os.path.join(folder, "1.tif")
    real_remove = executor_module.os.remove

    def fail_on_original(path):
        if str(path) == original:
            raise OSError(5, "связь потеряна")
        return real_remove(path)

    monkeypatch.setattr(executor_module.os, "remove", fail_on_original)
    monkeypatch.setattr(ProcessingService, "rollback_folder", lambda self, folder_path: _fake_rollback())
    v1.start(root)
    monkeypatch.undo()
    return v1


class PowerLoss(BaseException):
    """Не Exception: обработчики ошибок исполнителя его не ловят — как выключение питания."""


def crash_version_1_in_phase_a(tmp_path, monkeypatch, root, folder):
    """Версия 1 обрывается после записи .part-файлов: оригиналы на месте, папка выглядит необработанной."""
    v1 = version_1(tmp_path)
    real_copy = executor_module.safe_copy.copy_verified

    def copy_then_crash(source, destination, *args, **kwargs):
        copied = real_copy(source, destination, *args, **kwargs)
        if os.path.dirname(source) == folder and source.endswith(".tif"):
            raise PowerLoss()
        return copied

    monkeypatch.setattr(executor_module.safe_copy, "copy_verified", copy_then_crash)
    with pytest.raises(PowerLoss):
        v1.start(root)
    monkeypatch.undo()


def test_without_paths_to_version_1_processing_is_refused(tmp_path):
    root = tree_with_two_folders(tmp_path)
    paths = AppPaths(str(tmp_path / "Программы" / "Curator v2"))
    service = ProcessingService(settings=Settings(network_retry_delays_s=(0,)), paths=paths)

    with pytest.raises(PreconditionError) as error:
        service.start(root)
    assert "locks_dir" in str(error.value) and "legacy_journal_dirs" in str(error.value)
    assert listing(os.path.join(root, "Объект 001")) == ["1.tif", "2.tif"]


def test_missing_version_1_folders_are_not_created(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = version_2(tmp_path)

    with pytest.raises(PreconditionError) as error:
        service.start(root)
    message = str(error.value)
    assert "папка блокировок версии 1 не найдена" in message
    assert "журналы версии 1 недоступны" in message
    assert not os.path.exists(str(tmp_path / "Программы" / "Curator"))


def test_locks_are_shared_in_both_directions(tmp_path):
    root = tree_with_two_folders(tmp_path)
    version_1(tmp_path).check_environment()
    v1_locks = str(tmp_path / "Программы" / "Curator" / "locks")
    busy, free = (os.path.join(root, name) for name in ("Объект 001", "Объект 002"))
    seen_by_v1 = []

    def on_event(event):
        # Пока версия 2 обрабатывает папку, версия 1 не может её взять.
        if event["kind"] == ev.FOLDER_STARTED:
            with pytest.raises(FolderLocked):
                FolderLock(event["folder"], v1_locks).acquire()
            seen_by_v1.append(event["folder"])

    service = version_2(tmp_path, on_event=on_event)
    assert service.locks_dir == v1_locks
    v1_session = FolderLock(busy, v1_locks).acquire()
    try:
        summary = service.start(root)
    finally:
        v1_session.release()
    service.accept()

    assert seen_by_v1 == [free]
    assert summary.skipped_locked == 1
    assert listing(busy) == ["1.tif", "2.tif"]
    assert os.listdir(v1_locks) == []
    assert os.listdir(service.paths.locks) == []


def test_folder_unfinished_in_version_1_is_left_to_version_1(tmp_path, monkeypatch):
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 001")
    crash_version_1_in_phase_a(tmp_path, monkeypatch, root, folder)
    left_by_v1 = listing(folder)
    assert "1.tif" in left_by_v1 and any(name.endswith(".part") for name in left_by_v1)

    service = version_2(tmp_path)
    scanned = service.scan(root)
    assert any(problem.get("folder") == folder and "не завершена в версии 1" in problem["message"]
               for problem in service.problems.records)
    summary = service.start(scanned)
    service.accept()

    assert listing(folder) == left_by_v1
    assert [outcome.folder for outcome in summary.succeeded] == [os.path.join(root, "Объект 002")]
    assert not service.find_recovery()  # чужие незавершённые папки версия 2 не восстанавливает


def test_unfinished_in_version_1_is_skipped_even_if_folder_looks_ready(tmp_path):
    # Планировщик и так не берёт папку с .part-файлами; журнал версии 1 — вторая, независимая
    # защита: здесь папка снаружи выглядит готовой к обработке.
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 001")
    version_1(tmp_path).check_environment()
    service = version_2(tmp_path)
    service.legacy_unfinished = lambda: {normalize(folder): os.path.join("journal", "сессия.jsonl")}

    summary = service.start(root)
    service.accept()

    assert listing(folder) == ["1.tif", "2.tif"]
    assert summary.skipped_locked == 1
    assert any("завершите или откатите её в версии 1" in problem["message"]
               for problem in service.problems.records)


def test_conflict_left_by_version_1_is_explained_by_check(tmp_path, monkeypatch):
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 002")
    crash_version_1_in_phase_b(tmp_path, monkeypatch, root, folder)

    service = version_2(tmp_path)
    scanned = service.scan(root)

    assert folder not in [plan.folder for plan in scanned.to_process]
    assert any(problem.get("folder") == folder and "не завершена в версии 1" in problem["message"]
               for problem in service.problems.records)


def test_after_version_1_recovers_the_folder_is_free_again(tmp_path, monkeypatch):
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 002")
    crash_version_1_in_phase_b(tmp_path, monkeypatch, root, folder)

    v1_again = version_1(tmp_path)
    (journal, item), = v1_again.find_recovery()
    assert v1_again.recover(item, CONTINUE).complete
    v1_again.close()

    service = version_2(tmp_path)
    assert service.legacy_unfinished() == {}
    assert listing(folder) == ["ИСХ", "Рентгенограмма.jpeg"]
