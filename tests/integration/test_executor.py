import errno
import os

import pytest

from tests.conftest import gradient
from tests.tiff_writer import write_tiff
from tifjpg.domain import states
from tifjpg.domain.errors import IntegrityError, PreconditionError
from tifjpg.domain.folder_rules import classify
from tifjpg.domain.naming import XrayNaming
from tifjpg.fs import safe_copy
from tifjpg.fs.hashing import file_hash
from tifjpg.fs.scanner import read_folder
from tifjpg.transaction import executor as executor_module
from tifjpg.transaction.executor import ExecutionOptions, FolderExecutor
from tifjpg.transaction.journal import Journal, read_journal, session_header
from tifjpg.transaction.planner import plan_folder

pytestmark = pytest.mark.vips

naming = XrayNaming()
WIDTH, HEIGHT = 64, 32


def make_tiff(path):
    write_tiff(path, WIDTH, HEIGHT, gradient(WIDTH, HEIGHT, 0, 65535))
    return str(path)


def make_folder(tmp_path, names, in_archive=False, archive_name="ИСХ"):
    folder = tmp_path / "сеть" / "Объект 1"
    target = folder / archive_name if in_archive else folder
    target.mkdir(parents=True)
    for name in names:
        make_tiff(target / name)
    return str(folder)


def build_plan(folder):
    snapshot = read_folder(folder, naming)
    return plan_folder(snapshot, classify(snapshot, naming), naming)


def run_folder(folder, tmp_path, on_event=None, is_cancelled=None, **option_overrides):
    plan = build_plan(folder)
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    options = ExecutionOptions(temp_dir=str(local), delays=(0, 0), sleep=lambda seconds: None,
                               **option_overrides)
    journal_path = str(tmp_path / "journal.jsonl")
    journal = Journal(journal_path, header=session_header(folder, "test", "default_v1"))
    try:
        result = FolderExecutor(journal, options, on_event=on_event, is_cancelled=is_cancelled).run(plan)
    finally:
        journal.close()
    return result, plan, journal_path


def listing(path):
    return sorted(os.listdir(path))


def test_root_folder_is_processed_completely(vips, tmp_path):
    folder = make_folder(tmp_path, ["снимок 2.tif", "снимок 10.tif", "снимок 1.tif"])
    originals = {name: file_hash(os.path.join(folder, name)) for name in listing(folder)}

    result, plan, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_DONE
    assert [file.state for file in result.files] == [states.COMPLETED] * 3
    assert listing(folder) == ["ИСХ", "Рентгенограмма_1.jpeg", "Рентгенограмма_2.jpeg", "Рентгенограмма_3.jpeg"]
    assert listing(plan.archive_dir) == ["Рентгенограмма_1.tif", "Рентгенограмма_2.tif", "Рентгенограмма_3.tif"]
    # Оригиналы сохранены без изменений, порядок — натуральный.
    assert file_hash(os.path.join(plan.archive_dir, "Рентгенограмма_1.tif")) == originals["снимок 1.tif"]
    assert file_hash(os.path.join(plan.archive_dir, "Рентгенограмма_3.tif")) == originals["снимок 10.tif"]
    assert vips.Image.new_from_file(os.path.join(folder, "Рентгенограмма_1.jpeg")).width == WIDTH


def test_network_steps_report_progress_between_files(vips, tmp_path):
    # my_reports, этап 9: без этого сигнала статус в окне часами не менялся,
    # пока файл копировался по сети, и выглядело так, будто программа зависла.
    folder = make_folder(tmp_path, ["1.tif"])
    events = []

    run_folder(folder, tmp_path, on_event=events.append)

    stages = [event["stage"] for event in events
              if event["kind"] == "progress" and event.get("fraction") is None]
    assert stages == [states.STAGED, states.JPEG_UPLOADED, states.SOURCE_COPIED]


def test_archive_folder_keeps_originals_and_puts_jpegs_one_level_up(vips, tmp_path):
    folder = make_folder(tmp_path, ["1.tiff", "2.tiff"], in_archive=True)
    archive = os.path.join(folder, "ИСХ")
    originals = {name: file_hash(os.path.join(archive, name)) for name in listing(archive)}

    result, plan, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_DONE
    assert listing(folder) == ["ИСХ", "Рентгенограмма_1.jpeg", "Рентгенограмма_2.jpeg"]
    assert listing(archive) == ["Рентгенограмма_1.tif", "Рентгенограмма_2.tif"]
    assert file_hash(os.path.join(archive, "Рентгенограмма_1.tif")) == originals["1.tiff"]


def test_rename_inside_archive_does_not_overwrite_neighbour(tmp_path):
    # Имя второго файла займёт имя, которое должен получить первый.
    folder = make_folder(tmp_path, ["Рентгенограмма_2.tif", "снимок.tif"], in_archive=True)
    archive = os.path.join(folder, "ИСХ")
    before = {name: file_hash(os.path.join(archive, name)) for name in listing(archive)}

    result, plan, _ = run_folder(folder, tmp_path)

    assert plan.two_step_renames
    assert result.state == states.FOLDER_DONE
    assert listing(archive) == ["Рентгенограмма_1.tif", "Рентгенограмма_2.tif"]
    after = {name: file_hash(os.path.join(archive, name)) for name in listing(archive)}
    assert sorted(after.values()) == sorted(before.values())  # ни один снимок не потерян


def test_broken_tiff_leaves_folder_untouched(tmp_path):
    folder = make_folder(tmp_path, ["1.tif"])
    # Заголовок цел, а данные пикселей недоступны — так выглядит обрезанный файл.
    write_tiff(os.path.join(folder, "2.tif"), WIDTH, HEIGHT, gradient(WIDTH, HEIGHT, 0, 65535),
               strip_offset_delta=10 ** 7)
    before = listing(folder)

    result, plan, journal_path = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_FAILED
    assert listing(folder) == before  # ни JPEG, ни ИСХ, ни .part
    assert not os.path.exists(plan.archive_dir)
    assert any(entry.get("state") == states.CLEANED for entry in read_journal(journal_path))


def test_failure_on_second_file_removes_artifacts_of_the_first(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif", "2.tif", "3.tif"])
    before = listing(folder)
    real_copy = safe_copy.copy_verified
    calls = {"n": 0}

    def flaky(source, destination, **kwargs):
        calls["n"] += 1
        if calls["n"] == 4:  # второй файл, копирование оригинала в архив
            raise OSError(errno.EACCES, "отказано в доступе")
        return real_copy(source, destination, **kwargs)

    monkeypatch.setattr(executor_module.safe_copy, "copy_verified", flaky)
    result, plan, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_FAILED
    assert listing(folder) == before
    assert not os.path.exists(plan.archive_dir)


def test_transient_network_error_is_retried(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif"])
    real_copy_with_hash = executor_module.copy_with_hash
    attempts = {"n": 0}

    def flaky(source, destination, **kwargs):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError(errno.ETIMEDOUT, "сеть недоступна")
        return real_copy_with_hash(source, destination, **kwargs)

    monkeypatch.setattr(executor_module, "copy_with_hash", flaky)
    events = []
    result, _, journal_path = run_folder(folder, tmp_path, on_event=events.append)

    assert result.state == states.FOLDER_DONE
    assert attempts["n"] == 3
    assert [event for event in events if event["kind"] == "retry"]
    assert [entry for entry in read_journal(journal_path) if entry.get("action") == "retry"]


def test_permanent_error_is_not_retried(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif"])
    attempts = {"n": 0}

    def always_denied(source, destination, **kwargs):
        attempts["n"] += 1
        raise OSError(errno.EACCES, "отказано в доступе")

    monkeypatch.setattr(executor_module, "copy_with_hash", always_denied)
    result, _, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_FAILED
    assert attempts["n"] == 1


def test_cancel_between_files_keeps_originals(tmp_path):
    folder = make_folder(tmp_path, ["1.tif", "2.tif"])
    before = listing(folder)
    seen = {"files": 0}

    def cancelled():
        return seen["files"] >= 1

    def on_event(event):
        if event["kind"] == "file_prepared":
            seen["files"] += 1

    result, plan, _ = run_folder(folder, tmp_path, on_event=on_event, is_cancelled=cancelled)

    assert result.state == states.FOLDER_CANCELLED
    assert listing(folder) == before
    assert not os.path.exists(plan.archive_dir)


def test_copy_that_does_not_match_original_stops_the_folder(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif"])
    before = listing(folder)
    real_hash = safe_copy.file_hash

    def wrong_hash(path, **kwargs):
        digest = real_hash(path, **kwargs)
        return "sha256:0000" if path.endswith(".tif" + safe_copy.PART_SUFFIX) else digest

    monkeypatch.setattr(safe_copy, "file_hash", wrong_hash)
    result, _, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_FAILED
    assert "IntegrityError" in result.error
    assert listing(folder) == before


def test_original_changed_during_processing_is_not_deleted(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif"])
    monkeypatch.setattr(executor_module, "same_signature", lambda path, signature: False)

    result, plan, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_ROLLBACK_REQUIRED
    assert os.path.exists(os.path.join(folder, "1.tif"))  # оригинал на месте
    assert os.path.exists(os.path.join(plan.archive_dir, "Рентгенограмма_1.tif"))  # копия сделана
    assert "IntegrityError" in result.error


def test_not_enough_space_stops_before_any_change(tmp_path, monkeypatch):
    folder = make_folder(tmp_path, ["1.tif"])
    before = listing(folder)
    monkeypatch.setattr(safe_copy, "free_space", lambda path: 1024)

    result, plan, _ = run_folder(folder, tmp_path)

    assert result.state == states.FOLDER_FAILED
    assert "PreconditionError" in result.error
    assert listing(folder) == before
    assert not os.path.exists(plan.archive_dir)


def test_journal_records_intent_before_action(tmp_path):
    folder = make_folder(tmp_path, ["1.tif"])
    _, _, journal_path = run_folder(folder, tmp_path)
    entries = read_journal(journal_path)

    assert entries[0]["type"] == "session_start"
    file_states = [(entry["state"], entry["phase"]) for entry in entries if entry["type"] == "file_state"]
    for state in (states.PLANNED, states.STAGED, states.JPEG_UPLOADED, states.COMPLETED):
        assert (state, "intent") in file_states
        assert (state, "done") in file_states
    assert file_states.index((states.COMPLETED, "intent")) < file_states.index((states.COMPLETED, "done"))
    assert entries[-1]["type"] == "session_end"


def test_journal_tolerates_broken_last_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"seq": 1, "type": "note"}\n{"seq": 2, "type": "no', encoding="utf-8")
    assert [entry["seq"] for entry in read_journal(str(path))] == [1]
