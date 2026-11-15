import os

import pytest

from tests.integration.test_executor import listing, make_folder
from tests.integration.test_scanner import build_tree
from curator.app import events as ev
from curator.app.service import CONTINUE, ProcessingService
from curator.appdirs import AppPaths
from curator.config import Settings
from curator.domain import states
from curator.domain.errors import PreconditionError, CuratorError
from curator.fs.hashing import file_hash
from curator.fs.locks import FolderLock
from curator.transaction import executor as executor_module

pytestmark = pytest.mark.vips


# Версии 1 на диске теста нет — это указано явно, как требует 26.4.
STANDALONE = dict(locks_dir="locks", legacy_journal_dirs=())


def make_service(tmp_path, on_event=None, **settings):
    paths = AppPaths(str(tmp_path / "программа"))
    options = dict(STANDALONE, network_retry_delays_s=(0,))
    options.update(settings)
    service = ProcessingService(settings=Settings(**options), paths=paths, on_event=on_event)
    return service


def tree_with_two_folders(tmp_path):
    root = str(tmp_path / "сеть")
    for name, files in (("Объект 001", ["1.tif", "2.tif"]), ("Объект 002", ["1.tif"])):
        folder = os.path.join(root, name)
        os.makedirs(folder)
        from tests.integration.test_executor import make_tiff

        for file_name in files:
            make_tiff(os.path.join(folder, file_name))
    return root


def test_processes_every_folder_and_writes_journal_and_log(tmp_path):
    root = tree_with_two_folders(tmp_path)
    events = []
    service = make_service(tmp_path, on_event=events.append)

    summary = service.start(root)
    service.accept()

    assert len(summary.succeeded) == 2 and not summary.failed
    assert summary.files == 3
    assert listing(os.path.join(root, "Объект 001")) == [
        "ИСХ", "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    assert os.listdir(service.paths.journal) and os.listdir(service.paths.logs)
    kinds = [event["kind"] for event in events]
    for expected in (ev.SESSION_STARTED, ev.FOLDER_STARTED, ev.FOLDER_FINISHED,
                     ev.SESSION_FINISHED, ev.SESSION_ACCEPTED):
        assert expected in kinds
    log_text = open(os.path.join(service.paths.logs, os.listdir(service.paths.logs)[0]), encoding="utf-8").read()
    assert "Старт обработки" in log_text and "Обработка завершена" in log_text


def test_scan_reports_what_would_be_done(tmp_path):
    root = build_tree(str(tmp_path / "дерево"))
    service = make_service(tmp_path)

    result = service.scan(root)

    assert len(result.to_process) == 3
    assert len(result.by_kind("conflict")) == 1
    assert listing(os.path.join(root, "Объект 001")) == ["снимок 1.tif", "снимок 10.tif", "снимок 2.tif"]


def test_cancel_stops_before_next_folder(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = make_service(tmp_path)

    def on_event(event):
        if event["kind"] == ev.FOLDER_FINISHED:
            service.cancel()

    service._on_event = on_event
    summary = service.start(root)

    assert summary.cancelled
    assert len(summary.outcomes) == 1
    untouched = [folder for folder in os.listdir(root)
                 if listing(os.path.join(root, folder)) == ["1.tif", "2.tif"]]
    assert untouched  # вторая (бо́льшая) папка осталась как была: первой идёт та, где меньше файлов


def test_rollback_all_restores_every_folder(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = make_service(tmp_path)
    before = {os.path.join(path, name): file_hash(os.path.join(path, name))
              for path, _, files in os.walk(root) for name in files}

    service.start(root)
    results = service.rollback_all()
    service.close()

    assert all(result.state == "rolled_back" for result in results)
    after = {os.path.join(path, name): file_hash(os.path.join(path, name))
             for path, _, files in os.walk(root) for name in files}
    assert after == before


def test_accept_closes_the_session(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = make_service(tmp_path)
    summary = service.start(root)

    service.accept()

    assert summary.accepted
    with pytest.raises(CuratorError):
        service.rollback_folder(os.path.join(root, "Объект 001"))


def test_folder_locked_by_someone_else_is_skipped(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = make_service(tmp_path)
    service.check_environment()
    busy = os.path.join(root, "Объект 001")
    other_session = FolderLock(busy, service.paths.locks).acquire()
    try:
        summary = service.start(root)
    finally:
        other_session.release()

    assert summary.skipped_locked == 1
    assert len(summary.succeeded) == 1
    assert listing(busy) == ["1.tif", "2.tif"]  # чужую папку не тронули
    assert any("занята" in problem["message"] for problem in service.problems.records)


def test_failure_in_phase_b_triggers_automatic_rollback(tmp_path, monkeypatch):
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 002")
    service = make_service(tmp_path)
    before = file_hash(os.path.join(folder, "1.tif"))
    real_remove = executor_module.os.remove

    original = os.path.join(folder, "1.tif")

    def fail_on_original(path):
        if str(path) == original:  # только удаление самого оригинала
            raise OSError(5, "связь потеряна")
        return real_remove(path)

    monkeypatch.setattr(executor_module.os, "remove", fail_on_original)
    summary = service.start(root)
    monkeypatch.undo()

    failed = [outcome for outcome in summary.outcomes if outcome.folder == folder][0]
    assert failed.state == states.FOLDER_ROLLBACK_REQUIRED
    assert failed.rolled_back
    assert listing(folder) == ["1.tif"]
    assert file_hash(os.path.join(folder, "1.tif")) == before


def test_recovery_of_a_previous_session_is_offered(tmp_path, monkeypatch):
    root = tree_with_two_folders(tmp_path)
    folder = os.path.join(root, "Объект 002")
    paths = AppPaths(str(tmp_path / "программа"))
    first = ProcessingService(settings=Settings(network_retry_delays_s=(0,), **STANDALONE), paths=paths)
    real_remove = executor_module.os.remove

    original = os.path.join(folder, "1.tif")

    def fail_on_original(path):
        if str(path) == original:
            raise OSError(5, "связь потеряна")
        return real_remove(path)

    # Прерываем фазу B и не даём сессии закрыться — так выглядит сбой питания.
    monkeypatch.setattr(executor_module.os, "remove", fail_on_original)
    monkeypatch.setattr(ProcessingService, "rollback_folder", lambda self, folder_path: _fake_rollback())
    first.start(root)
    monkeypatch.undo()

    second = ProcessingService(settings=Settings(network_retry_delays_s=(0,), **STANDALONE), paths=paths)
    found = second.find_recovery()

    assert [item.folder for _, item in found] == [folder]
    result = second.recover(found[0][1], CONTINUE)
    second.close()
    assert result.complete
    assert listing(folder) == ["ИСХ", "Рентгенограмма.jpeg"]


class _FakeRollback:
    state = "rolled_back"
    warnings = ()


def _fake_rollback():
    return _FakeRollback()


def test_environment_without_write_access_is_refused(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    monkeypatch.setattr(service.paths, "prepare", lambda: ["журнал: отказано в доступе"])

    with pytest.raises(PreconditionError) as error:
        service.check_environment()
    assert "обработка невозможна" in str(error.value)


def test_folders_are_processed_by_ascending_file_count(tmp_path):
    root = tree_with_two_folders(tmp_path)      # «Объект 001» — 2 файла, «Объект 002» — 1
    started = []
    service = make_service(tmp_path)
    service._on_event = lambda event: started.append(os.path.basename(event["folder"])) \
        if event["kind"] == ev.FOLDER_STARTED else None
    scanned = service.scan(root)
    assert [os.path.basename(plan.folder) for plan in scanned.to_process] == ["Объект 002", "Объект 001"]
    service.start(scanned)
    assert started == ["Объект 002", "Объект 001"]


def test_log_contains_timings_per_stage_and_cpu(tmp_path):
    root = tree_with_two_folders(tmp_path)
    service = make_service(tmp_path)

    service.start(root)
    service.accept()

    log_text = open(os.path.join(service.paths.logs, os.listdir(service.paths.logs)[0]), encoding="utf-8").read()
    assert "Замер:" in log_text
    assert "чтение оригинала" in log_text and "конвертация" in log_text and "запись JPEG" in log_text
    assert "процессор Curator" in log_text


# --- D26: готовая папка, «рентгенограмма….jpg» -> «….jpeg» ------------------------------

def done_folder_with_jpg(tmp_path):
    root = str(tmp_path / "сеть")
    folder = os.path.join(root, "Объект 003")
    os.makedirs(os.path.join(folder, "ИСХ"))
    for name, data in (("Рентгенограмма 1.jpg", b"jpeg-1"), ("Рентгенограмма 2.JPG", b"jpeg-2"),
                       ("ИСХ/снимок а.tif", b"tif-a"), ("ИСХ/снимок б.tif", b"tif-b")):
        with open(os.path.join(folder, name), "wb") as stream:
            stream.write(data)
    return root, folder


def test_done_folder_jpg_renamed_to_jpeg_and_rolled_back(tmp_path):
    root, folder = done_folder_with_jpg(tmp_path)
    events = []
    service = make_service(tmp_path, on_event=events.append)

    scan = service.scan(root)
    assert len(scan.to_rename) == 1 and scan.total_renames == 2 and not scan.to_process

    summary = service.start(root)
    assert summary.renamed_folders == 1 and summary.renamed_files == 2 and not summary.failed
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    assert listing(os.path.join(folder, "ИСХ")) == ["снимок а.tif", "снимок б.tif"]  # архив не трогаем
    with open(os.path.join(folder, "Рентгенограмма 2.jpeg"), "rb") as stream:
        assert stream.read() == b"jpeg-2"
    renamed = [event for event in events if event["kind"] == ev.FOLDER_RENAMED]
    assert renamed and renamed[0]["state"] == states.FOLDER_DONE and renamed[0]["files"] == 2

    result = service.rollback_folder(folder)
    assert result.complete
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpg", "Рентгенограмма 2.JPG"]

    # Повторный откат ничего не ломает.
    assert service.rollback_folder(folder).state == "nothing_to_roll_back"

    # После отката папку можно снова привести к .jpeg — следующий запуск сделает то же самое.
    again = make_service(tmp_path).start(root)
    assert again.renamed_files == 2


def test_jpg_rename_is_written_to_the_journal_and_readable_back(tmp_path):
    from curator.transaction.journal import read_journal
    from curator.transaction.records import folders_from_journal
    from curator.transaction import recovery

    root, folder = done_folder_with_jpg(tmp_path)
    service = make_service(tmp_path)
    service.start(root)
    path = os.path.join(service.paths.journal, os.listdir(service.paths.journal)[0])

    record = folders_from_journal(read_journal(path))[folder]
    assert sorted(os.path.basename(item.target) for item in record.renames.values()) == [
        "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    assert all(item.done for item in record.renames.values())
    # Переименование атомарно: после сбоя разбирать нечего.
    assert not recovery.analyse(record).needs_attention

    # Откат по журналу (как после сбоя) возвращает старые имена.
    from curator.transaction.rollback import rollback_folder
    from curator.transaction.journal import Journal

    with Journal(str(tmp_path / "откат.jsonl")) as journal:
        assert rollback_folder(record, journal).complete
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpg", "Рентгенограмма 2.JPG"]


def test_failure_in_the_middle_of_jpg_renames_is_rolled_back(tmp_path, monkeypatch):
    from curator.fs import safe_copy

    root, folder = done_folder_with_jpg(tmp_path)
    service = make_service(tmp_path)
    real_rename = safe_copy.safe_rename
    calls = []

    def fail_second(source, destination):
        calls.append(source)
        if len(calls) == 2:
            raise OSError(5, "связь потеряна")
        return real_rename(source, destination)

    monkeypatch.setattr(safe_copy, "safe_rename", fail_second)
    monkeypatch.setattr("curator.transaction.renamer.safe_copy.safe_rename", fail_second)
    summary = service.start(root)
    monkeypatch.undo()

    failed = [outcome for outcome in summary.outcomes if outcome.folder == folder][0]
    assert failed.state == states.FOLDER_ROLLBACK_REQUIRED and failed.rolled_back
    assert summary.renamed_folders == 0
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpg", "Рентгенограмма 2.JPG"]


def test_conflict_folder_with_jpg_is_not_touched(tmp_path):
    root, folder = done_folder_with_jpg(tmp_path)
    os.remove(os.path.join(folder, "ИСХ", "снимок б.tif"))  # JPEG 2, TIFF 1 — конфликт

    summary = make_service(tmp_path).start(root)
    assert summary.renamed_folders == 0
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpg", "Рентгенограмма 2.JPG"]
