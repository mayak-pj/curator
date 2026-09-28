import os

import pytest

from tests.integration.test_executor import listing, make_folder, run_folder
from tifjpg.domain import states
from tifjpg.fs import safe_copy
from tifjpg.fs.hashing import file_hash
from tifjpg.transaction import executor as executor_module
from tifjpg.transaction.journal import Journal, read_journal, session_header
from tifjpg.transaction.records import folders_from_journal
from tifjpg.transaction.recovery import (
    CONSISTENT,
    PHASE_A_LEFTOVERS,
    PHASE_B_UNFINISHED,
    analyse,
    find_unfinished,
    finish,
    inspect,
)
from tifjpg.transaction.rollback import ROLLED_BACK, rollback_folder

pytestmark = pytest.mark.vips


def interrupt_phase_b(tmp_path, monkeypatch, names=("1.tif", "2.tif")):
    """Прервать фиксацию так же, как это сделал бы сбой питания."""
    folder = make_folder(tmp_path, list(names))
    before = {name: file_hash(os.path.join(folder, name)) for name in listing(folder)}
    real_remove = executor_module.os.remove

    def fail_on_delete(path):
        # Только удаление оригинала в самой папке: временные файлы удаляются штатно.
        if str(path).startswith(folder) and str(path).endswith(".tif"):
            raise OSError(5, "связь потеряна")
        return real_remove(path)

    monkeypatch.setattr(executor_module.os, "remove", fail_on_delete)
    result, plan, journal_path = run_folder(folder, tmp_path)
    monkeypatch.undo()
    assert result.state == states.FOLDER_ROLLBACK_REQUIRED
    record = folders_from_journal(read_journal(journal_path))[folder]
    return folder, plan, record, before, journal_path


def test_interrupted_phase_b_is_detected(tmp_path, monkeypatch):
    folder, plan, record, _, _ = interrupt_phase_b(tmp_path, monkeypatch)

    recovery = analyse(record)

    assert recovery.situation == PHASE_B_UNFINISHED
    assert recovery.needs_attention
    assert recovery.details


def test_continue_finishes_the_work(tmp_path, monkeypatch):
    folder, plan, record, before, _ = interrupt_phase_b(tmp_path, monkeypatch)
    journal = Journal(str(tmp_path / "recovery.jsonl"))

    result = finish(record, journal)
    journal.close()

    assert result.complete
    assert listing(folder) == ["ИСХ", "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    assert listing(plan.archive_dir) == ["Рентгенограмма 1.tif", "Рентгенограмма 2.tif"]
    after = {name: file_hash(os.path.join(plan.archive_dir, name)) for name in listing(plan.archive_dir)}
    assert sorted(after.values()) == sorted(before.values())
    assert analyse(record).situation == CONSISTENT


def test_rollback_after_interrupted_phase_b(tmp_path, monkeypatch):
    folder, plan, record, before, _ = interrupt_phase_b(tmp_path, monkeypatch)
    journal = Journal(str(tmp_path / "rollback.jsonl"))

    result = rollback_folder(record, journal)
    journal.close()

    assert result.state == ROLLED_BACK
    assert listing(folder) == ["1.tif", "2.tif"]
    assert {name: file_hash(os.path.join(folder, name)) for name in listing(folder)} == before


def test_finish_is_idempotent(tmp_path, monkeypatch):
    folder, plan, record, _, _ = interrupt_phase_b(tmp_path, monkeypatch)
    journal = Journal(str(tmp_path / "recovery.jsonl"))
    first = finish(record, journal)
    second = finish(record, journal)
    journal.close()

    assert first.actions and not second.actions
    assert second.complete


def test_finish_keeps_original_if_archive_copy_was_changed(tmp_path, monkeypatch):
    folder, plan, record, _, _ = interrupt_phase_b(tmp_path, monkeypatch, names=("1.tif",))
    with open(os.path.join(plan.archive_dir, "Рентгенограмма.tif"), "ab") as stream:
        stream.write("подмена".encode("utf-8"))
    journal = Journal(str(tmp_path / "recovery.jsonl"))

    result = finish(record, journal)
    journal.close()

    assert not result.complete
    assert any("не совпадает с журналом" in warning for warning in result.warnings)
    assert os.path.exists(os.path.join(folder, "1.tif"))


def test_leftover_part_files_are_reported(tmp_path, monkeypatch):
    folder, plan, record, _, _ = interrupt_phase_b(tmp_path, monkeypatch, names=("1.tif",))
    journal = Journal(str(tmp_path / "recovery.jsonl"))
    finish(record, journal)
    journal.close()
    # Имитируем брошенный временный файл от прерванной фазы A.
    open(safe_copy.part_path(os.path.join(folder, "Рентгенограмма.jpeg")), "wb").close()

    assert analyse(record).situation == PHASE_A_LEFTOVERS


def test_find_unfinished_lists_only_broken_sessions(tmp_path):
    folder = make_folder(tmp_path, ["1.tif"])
    journals = tmp_path / "journal"
    journals.mkdir()

    finished = Journal(str(journals / "finished.jsonl"), header=session_header(folder, "t", "default_v1"))
    finished.folder_state(folder, states.FOLDER_DONE)
    finished.close()

    broken = Journal(str(journals / "broken.jsonl"), header=session_header(folder, "t", "default_v1"))
    broken.folder_state(folder, "started")
    broken._stream.close()  # сессия оборвалась без записи о завершении

    needs_rollback = Journal(str(journals / "needs_rollback.jsonl"),
                             header=session_header(folder, "t", "default_v1"))
    needs_rollback.folder_state(folder, states.FOLDER_ROLLBACK_REQUIRED)
    needs_rollback.close()

    found = [os.path.basename(path) for path in find_unfinished(str(journals))]
    assert found == ["broken.jsonl", "needs_rollback.jsonl"]


def test_inspect_returns_folder_situations(tmp_path, monkeypatch):
    folder, plan, record, _, journal_path = interrupt_phase_b(tmp_path, monkeypatch, names=("1.tif",))

    recoveries = inspect(journal_path)

    assert [item.folder for item in recoveries] == [folder]
    assert recoveries[0].situation == PHASE_B_UNFINISHED
