import pytest

from tifjpg.app.service import FolderOutcome, SessionSummary
from tifjpg.domain import states
from tifjpg.domain.folder_rules import classify
from tifjpg.domain.models import FolderSnapshot
from tifjpg.domain.naming import XrayNaming
from tifjpg.gui import status as gui_status
from tifjpg.transaction.planner import plan_folder
from tifjpg.transaction.rollback import NOTHING, PARTIAL, ROLLED_BACK

naming = XrayNaming()


def plan_for(files=(), archive_dir=None, archive_files=()):
    snapshot = FolderSnapshot(path="/work/Объект 1", files=tuple(files),
                              archive_dir=archive_dir, archive_files=tuple(archive_files))
    return plan_folder(snapshot, classify(snapshot, naming), naming)


def test_folder_to_process_is_neutral_with_file_count():
    text, colour = gui_status.initial_status(plan_for(["1.tif", "2.tif"]))
    assert "2 файлов" in text
    assert colour == gui_status.NEUTRAL


def test_already_processed_folder_is_green():
    text, colour = gui_status.initial_status(plan_for(["1.tif", "Рентгенограмма_1.jpeg"]))
    assert colour == gui_status.SUCCESS
    assert "не обрабатывалась" in text


def test_conflict_is_amber_with_reason():
    text, colour = gui_status.initial_status(plan_for(["1.tif", "2.tif", "Рентгенограмма_1.jpeg"]))
    assert colour == gui_status.WARNING
    assert "не совпадает" in text


def test_blocked_folder_is_red():
    text, colour = gui_status.initial_status(plan_for(["1.tif", "Рентгенограмма_1.jpeg.part"]))
    assert colour == gui_status.ERROR
    assert "не может быть обработана" in text


@pytest.mark.parametrize("state, expected_colour", [
    (states.FOLDER_DONE, gui_status.SUCCESS),
    (states.FOLDER_RUNNING, gui_status.RUNNING),
    (states.FOLDER_FAILED, gui_status.ERROR),
    (states.FOLDER_CANCELLED, gui_status.WARNING),
])
def test_folder_states_have_colours(state, expected_colour):
    assert gui_status.folder_status(state, error="причина")[1] == expected_colour


def test_rollback_required_turns_amber_once_rolled_back():
    assert gui_status.folder_status(states.FOLDER_ROLLBACK_REQUIRED, "сбой")[1] == gui_status.ERROR
    text, colour = gui_status.folder_status(states.FOLDER_ROLLBACK_REQUIRED, "сбой", rolled_back=True)
    assert colour == gui_status.WARNING
    assert "откат выполнен" in text


def test_rollback_offered_only_where_disk_changed():
    assert gui_status.can_rollback(states.FOLDER_DONE)
    assert gui_status.can_rollback(states.FOLDER_ROLLBACK_REQUIRED)
    assert not gui_status.can_rollback(states.FOLDER_FAILED)
    assert not gui_status.can_rollback(states.FOLDER_CANCELLED)


def test_rollback_statuses():
    assert gui_status.rollback_status(ROLLED_BACK)[0] == "откат выполнен"
    assert gui_status.rollback_status(NOTHING)[0] == "откатывать нечего"
    text, colour = gui_status.rollback_status(PARTIAL, ["JPEG изменён"])
    assert colour == gui_status.WARNING and "JPEG изменён" in text


def test_summary_line_counts_everything():
    summary = SessionSummary(root="/work", done_folders=2, conflicts=1, skipped_locked=1, seconds=12.4)
    summary.outcomes = [
        FolderOutcome(folder="a", state=states.FOLDER_DONE, files=3),
        FolderOutcome(folder="b", state=states.FOLDER_FAILED, files=1, error="сбой"),
    ]
    line = gui_status.summary_line(summary)
    assert "Обработано файлов 3" in line
    assert "успешно 1" in line and "с ошибками 1" in line
    assert "уже готовых 2" in line and "конфликтов 1" in line and "занято другими 1" in line
