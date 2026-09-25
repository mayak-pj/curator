import os

import pytest

from tests.integration.test_executor import build_plan, listing, make_folder, run_folder
from tifjpg.domain import states
from tifjpg.fs.hashing import file_hash
from tifjpg.transaction.journal import Journal, read_journal
from tifjpg.transaction.records import folder_from_result, folders_from_journal
from tifjpg.transaction.rollback import NOTHING, PARTIAL, ROLLED_BACK, rollback_folder

pytestmark = pytest.mark.vips


def rollback(record, tmp_path, name="rollback.jsonl"):
    journal = Journal(str(tmp_path / name))
    try:
        return rollback_folder(record, journal)
    finally:
        journal.close()


def process_and_record(tmp_path, names, in_archive=False):
    folder = make_folder(tmp_path, names, in_archive=in_archive)
    source_dir = os.path.join(folder, "ИСХ") if in_archive else folder
    before = {name: file_hash(os.path.join(source_dir, name)) for name in listing(source_dir)}
    result, plan, _ = run_folder(folder, tmp_path)
    assert result.state == states.FOLDER_DONE
    return folder, plan, folder_from_result(plan, result), before


def test_rollback_returns_folder_to_original_state(tmp_path):
    folder, plan, record, before = process_and_record(tmp_path, ["снимок 1.tif", "снимок 2.tif"])

    result = rollback(record, tmp_path)

    assert result.state == ROLLED_BACK
    assert listing(folder) == ["снимок 1.tif", "снимок 2.tif"]
    assert not os.path.exists(plan.archive_dir)  # папку ИСХ создавали мы
    after = {name: file_hash(os.path.join(folder, name)) for name in listing(folder)}
    assert after == before


def test_rollback_restores_names_inside_archive(tmp_path):
    folder, plan, record, before = process_and_record(tmp_path, ["1.tiff", "2.tiff"], in_archive=True)

    result = rollback(record, tmp_path)

    assert result.state == ROLLED_BACK
    assert listing(folder) == ["ИСХ"]  # JPEG удалены, ИСХ не наша — осталась
    assert listing(plan.archive_dir) == ["1.tiff", "2.tiff"]
    after = {name: file_hash(os.path.join(plan.archive_dir, name)) for name in listing(plan.archive_dir)}
    assert after == before


def test_modified_jpeg_is_never_deleted(tmp_path):
    folder, plan, record, before = process_and_record(tmp_path, ["1.tif"])
    edited = os.path.join(folder, "Рентгенограмма_1.jpeg")
    with open(edited, "ab") as stream:  # пользователь что-то дописал в файл
        stream.write("правка пользователя".encode("utf-8"))

    result = rollback(record, tmp_path)

    assert result.state == PARTIAL
    assert os.path.exists(edited)
    assert any("изменён после обработки" in warning for warning in result.warnings)
    assert os.path.exists(os.path.join(folder, "1.tif"))  # оригинал всё равно вернулся


def test_missing_archive_copy_keeps_the_jpeg(tmp_path):
    """Если оригинал восстановить нечем, JPEG — единственная копия снимка."""
    folder, plan, record, _ = process_and_record(tmp_path, ["1.tif"])
    os.remove(os.path.join(plan.archive_dir, "Рентгенограмма_1.tif"))

    result = rollback(record, tmp_path)

    assert result.state == PARTIAL
    assert os.path.exists(os.path.join(folder, "Рентгенограмма_1.jpeg"))
    assert any("восстановить нечем" in warning for warning in result.warnings)
    assert any("JPEG оставлен" in warning for warning in result.warnings)


def test_modified_archive_copy_is_not_used(tmp_path):
    folder, plan, record, _ = process_and_record(tmp_path, ["1.tif"])
    with open(os.path.join(plan.archive_dir, "Рентгенограмма_1.tif"), "ab") as stream:
        stream.write("подмена".encode("utf-8"))

    result = rollback(record, tmp_path)

    assert result.state == PARTIAL
    assert any("изменена" in warning for warning in result.warnings)
    assert os.path.exists(os.path.join(plan.archive_dir, "Рентгенограмма_1.tif"))
    assert os.path.exists(os.path.join(folder, "Рентгенограмма_1.jpeg"))


def test_rollback_after_failed_phase_a_has_nothing_to_do(tmp_path, monkeypatch):
    from tifjpg.transaction import executor as executor_module

    folder = make_folder(tmp_path, ["1.tif"])
    monkeypatch.setattr(executor_module, "copy_with_hash",
                        lambda *args, **kwargs: (_ for _ in ()).throw(OSError(13, "нет доступа")))
    result, plan, _ = run_folder(folder, tmp_path)
    assert result.state == states.FOLDER_FAILED

    rolled = rollback(folder_from_result(plan, result), tmp_path)

    assert rolled.state == NOTHING
    assert listing(folder) == ["1.tif"]


def test_rollback_can_use_the_journal_of_a_previous_session(tmp_path):
    folder, plan, _, before = process_and_record(tmp_path, ["1.tif", "2.tif"])
    entries = read_journal(str(tmp_path / "journal.jsonl"))
    record = folders_from_journal(entries)[folder]

    result = rollback(record, tmp_path, name="rollback2.jsonl")

    assert result.state == ROLLED_BACK
    assert listing(folder) == ["1.tif", "2.tif"]
    assert {name: file_hash(os.path.join(folder, name)) for name in listing(folder)} == before
