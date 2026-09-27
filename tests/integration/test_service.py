import os

import pytest

from tests.integration.test_executor import listing, make_folder
from tests.integration.test_scanner import build_tree
from tifjpg.app import events as ev
from tifjpg.app.service import CONTINUE, ProcessingService
from tifjpg.appdirs import AppPaths
from tifjpg.config import Settings
from tifjpg.domain import states
from tifjpg.domain.errors import PreconditionError, TifJpgError
from tifjpg.fs.hashing import file_hash
from tifjpg.fs.locks import FolderLock
from tifjpg.transaction import executor as executor_module

pytestmark = pytest.mark.vips


def make_service(tmp_path, on_event=None, **settings):
    paths = AppPaths(str(tmp_path / "программа"))
    service = ProcessingService(settings=Settings(network_retry_delays_s=(0,), **settings),
                                paths=paths, on_event=on_event)
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
                 if listing(os.path.join(root, folder)) == ["1.tif"]]
    assert untouched  # вторая папка осталась как была


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
    with pytest.raises(TifJpgError):
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
    first = ProcessingService(settings=Settings(network_retry_delays_s=(0,)), paths=paths)
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

    second = ProcessingService(settings=Settings(network_retry_delays_s=(0,)), paths=paths)
    found = second.find_recovery()

    assert [item.folder for _, item in found] == [folder]
    result = second.recover(found[0][1], CONTINUE)
    second.close()
    assert result.complete
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpeg"]


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
