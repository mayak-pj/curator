"""Окно обработки на Qt (этап 13, чек-лист 26.5) без экрана, на настоящем сервисе и файлах."""

import json
import os
import sys

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from tests.integration.test_executor import listing
from tests.integration.test_service import tree_with_two_folders
from curator.app import events as ev
from curator.app.service import ROLLBACK
from curator.appdirs import AppPaths
from curator.domain import states
from curator.domain.errors import PreconditionError

pytestmark = pytest.mark.vips

STANDALONE = {"locks_dir": "locks", "legacy_journal_dirs": [], "network_retry_delays_s": [0]}


@pytest.fixture
def offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        monkeypatch.setenv("QT_QPA_FONTDIR", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))


def program(tmp_path, config=STANDALONE):
    base = tmp_path / "Программы" / "Curator v2"
    base.mkdir(parents=True)
    if config is not None:
        (base / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return AppPaths(str(base))


def open_page(paths, interactive=False):
    from curator.platform import app, modules
    from curator.platform.start_window import StartWindow

    qt_app, context, _ = app._create_app(paths, interactive=interactive)
    window = StartWindow(context)
    window.open_module(modules.get("xray"))
    return qt_app, window, window.module_page


@pytest.mark.parametrize("scenario", ["accept", "rollback"])
def test_selftest_walks_the_buttons_on_real_files(tmp_path, offscreen, monkeypatch, scenario):
    from curator.platform import app
    from curator.modules.xray import selftest

    root = tree_with_two_folders(tmp_path)
    paths = program(tmp_path)
    monkeypatch.setattr(app.appdirs, "AppPaths", lambda: paths)
    out = tmp_path / "отчёт"

    code = app.run(["--selftest", scenario, root, str(out)])

    report = (out / selftest.REPORT_NAME).read_text(encoding="utf-8")
    assert code == 0, report
    assert "ИТОГ: ошибок нет" in report
    assert "папка «Объект 001»: готово · откат=1" in report
    for name in ("scan", "done", "finished"):
        assert (out / "xray_{}_dark.png".format(name)).stat().st_size > 1000
    if scenario == "accept":
        assert listing(os.path.join(root, "Объект 001")) == ["ИСХ", "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    else:
        assert listing(os.path.join(root, "Объект 001")) == ["1.tif", "2.tif"]
        assert listing(os.path.join(root, "Объект 002")) == ["1.tif"]
    assert os.listdir(os.path.join(paths.base, "locks")) == []
    assert "last_root" in json.loads(open(paths.gui_state, encoding="utf-8").read())


def test_progress_and_labels_follow_service_events(tmp_path, offscreen):
    """Номер файла в строке папки и под полосой — из одного события; полоса не отступает назад."""
    from curator.modules.xray import status

    qt_app, window, page = open_page(program(tmp_path))
    folder = os.path.join(str(tmp_path), "Объект")
    post = page._handle_event
    post(ev.event(ev.SESSION_STARTED, files=4))
    post(ev.event(ev.FOLDER_STARTED, folder=folder, files=4))
    row = page.folders.rows[folder]
    assert row.running and row.status.property("tone") == status.RUNNING
    post(ev.event(ev.FILE_STARTED, folder=folder, index=2))
    post(ev.event(ev.PROGRESS, stage=states.SOURCE_COPIED, fraction=0.5, index=2))
    assert row.status.text() == "обрабатывается: файл 2 из 4"
    assert page.stage_label.text() == "Файл #2: перенос оригинала в архив 50 %"
    value = page.progress.value()
    assert value > 0 and "%" in page.current_label.text()
    post(ev.event(ev.PROGRESS, stage=states.SOURCE_COPIED, fraction=0.0, index=2))  # повтор после сбоя
    assert page.progress.value() == value
    post(ev.event(ev.FOLDER_FINISHED, folder=folder, state=states.FOLDER_DONE))
    assert row.status.text() == "готово" and row.button.isEnabled() and not row.running
    post(ev.event(ev.FOLDER_SKIPPED, folder=folder + "2", reason="занята: Иванов"))
    assert page.errors.count == 1 and "Иванов" in page.errors.text()
    window.close()


def test_without_write_access_processing_is_refused_with_message(tmp_path, offscreen, monkeypatch):
    from curator.modules.xray import selftest

    root = tree_with_two_folders(tmp_path)
    qt_app, window, page = open_page(program(tmp_path))

    def refuse(self):
        raise PreconditionError("нет доступа на запись в папку программы — обработка невозможна")

    monkeypatch.setattr(type(page.service), "check_environment", refuse)
    page.root_edit.setText(root)
    page.start_button.click()
    selftest.wait_idle(page, qt_app)

    assert page.notices and "нет доступа на запись" in page.notices[-1]
    assert page.status_label.text() == "Ошибка" and page.errors.count == 1
    assert page.start_button.isEnabled() and not page.accept_button.isEnabled()
    assert listing(os.path.join(root, "Объект 001")) == ["1.tif", "2.tif"]
    window.close()


def test_page_without_settings_shows_why_and_still_scans(tmp_path, offscreen):
    qt_app, window, page = open_page(program(tmp_path, config=None))
    assert not page.ready and "locks_dir" in page.status_text.text()
    assert page.scan_button.isEnabled()          # «Проверить» ничего не меняет — не блокируется
    window.close()


def test_readiness_card_is_recomputed_when_the_problem_goes_away(tmp_path, offscreen):
    paths = program(tmp_path, config={"locks_dir": "../Curator/locks", "legacy_journal_dirs": ["../Curator/journal"]})
    qt_app, window, page = open_page(paths)
    assert not page.ready and not page.card.isHidden()
    legacy = tmp_path / "Программы" / "Curator"
    (legacy / "locks").mkdir(parents=True)
    (legacy / "journal").mkdir()
    page.scan_button.click()
    assert page.ready and page.card.isHidden()
    window.close()


def test_closing_during_processing_asks_and_cancels(tmp_path, offscreen, monkeypatch):
    import threading

    qt_app, window, page = open_page(program(tmp_path))
    release = threading.Event()
    page.worker.submit("start", release.wait, 5)
    cancelled = []
    monkeypatch.setattr(page.service, "cancel", lambda: cancelled.append(True))

    window.close()
    assert cancelled and not page.confirm_close()
    release.set()
    from curator.modules.xray import selftest

    selftest.wait_idle(page, qt_app)
    assert page.confirm_close()
    window.close()


def test_recovery_dialog_offers_choice_and_applies_it(tmp_path, offscreen):
    from curator.modules.xray.widgets import RecoveryDialog

    class Item:
        folder = "Z:\\Объект 7"
        situation = "phase_b_unfinished"
        details = ["Рентгенограмма 1.jpeg уже на месте"]

    item = Item()
    qt_app, window, page = open_page(program(tmp_path))
    dialog = RecoveryDialog([("journal.jsonl", item)], page)
    assert dialog.selected() == []               # по умолчанию — «Игнорировать», ничего не делается
    dialog.choose(item, ROLLBACK)
    dialog._apply()
    assert dialog.decisions == [(item, ROLLBACK)]
    window.close()


def test_automatic_rollback_is_not_offered_again_and_errors_are_not_doubled(tmp_path, offscreen):
    """Исполнитель и сервис оба сообщают об окончании папки; после автоотката (D11) «Откатить» не включается."""
    qt_app, window, page = open_page(program(tmp_path))
    broken, failed = os.path.join(str(tmp_path), "Объект 1"), os.path.join(str(tmp_path), "Объект 2")
    post = page._handle_event
    post(ev.event(ev.FOLDER_FINISHED, folder=broken, state=states.FOLDER_ROLLBACK_REQUIRED, error="сеть"))
    post(ev.event(ev.ROLLBACK_FINISHED, folder=broken, state="rolled_back", warnings=[]))
    post(ev.event(ev.FOLDER_FINISHED, folder=broken, state=states.FOLDER_ROLLBACK_REQUIRED, error="сеть",
                  rolled_back=True))
    for _ in range(2):
        post(ev.event(ev.FOLDER_FINISHED, folder=failed, state=states.FOLDER_FAILED, error="нет места"))
    page._idle()

    assert page.folders.rows[broken].status.text() == "ошибка, папка возвращена в исходное состояние: сеть"
    assert not page.folders.rows[broken].button.isEnabled() and page.processed_folders == []
    assert page.errors.text().count("нет места") == 1
    assert not page.accept_button.isEnabled()
    window.close()


def test_file_flow_follows_events_and_does_not_rush_the_final_names(tmp_path, offscreen):
    """Сцена идёт за событиями; «готово» и полёт в архив — только по file_completed (фаза B — пачкой)."""
    from curator.modules.xray import flow

    root = tree_with_two_folders(tmp_path)
    qt_app, window, page = open_page(program(tmp_path))
    result = page.service.scan(root)
    page._on_scan_finished(result)
    plan = [p for p in result.plans if p.files][0]
    folder, post = plan.folder, page._handle_event

    post(ev.event(ev.FOLDER_STARTED, folder=folder, files=len(plan.files)))
    tiles = page.flow.rows
    assert sorted(tiles) == [f.index for f in plan.files]
    first = tiles[1]
    assert first.before == "1.tif" and first.after == "Рентгенограмма 1.jpeg + ИСХ\\Рентгенограмма 1.tif"
    assert first.phase == flow.WAITING

    post(ev.event(ev.FILE_STARTED, folder=folder, index=1))
    post(ev.event(ev.PROGRESS, stage=states.STAGED, fraction=1.0, index=1))
    post(ev.event(ev.PROGRESS, stage="encode", fraction=0.5, index=1))
    assert first.phase == flow.RUNNING and first.current == "convert" and first.fraction == 0.5
    assert states.STAGED in first.done_steps and states.JPEG_UPLOADED not in first.done_steps
    assert "TIF" not in page.flow.scene.caption and "1.tif" in page.flow.scene.caption

    assert first.fly_at is None                                        # копия в архиве ещё не записана
    post(ev.event(ev.PROGRESS, stage=states.SOURCE_COPIED, fraction=1.0, index=1))
    assert first.fly_at is not None                                    # оригинал в архиве: летит туда
    post(ev.event(ev.FILE_PREPARED, folder=folder, index=1))
    assert first.phase == flow.PREPARED and first.done_at is None      # имена ещё не зафиксированы
    post(ev.event(ev.FILE_COMPLETED, folder=folder, index=1))
    post(ev.event(ev.FILE_COMPLETED, folder=folder, index=2))
    assert first.phase == flow.COMPLETED and first.done_at is not None
    assert tiles[2].done_at > first.done_at                            # пачка фазы B выстраивается в очередь
    assert page.flow.scene.grab().width() > 0                          # кадр рисуется без ошибок
    post(ev.event(ev.FOLDER_FINISHED, folder=folder, state=states.FOLDER_DONE))
    # итог папки держится на экране: следующая папка открывается не сразу, а через очередь
    post(ev.event(ev.FOLDER_STARTED, folder=folder, files=len(plan.files)))
    assert page.flow.rows[1].phase == flow.COMPLETED and len(page.flow._queue) == 1
    window.close()


def test_file_flow_switch_is_remembered_and_stops_building_rows(tmp_path, offscreen):
    from curator.platform import state

    root = tree_with_two_folders(tmp_path)
    paths = program(tmp_path)
    qt_app, window, page = open_page(paths)
    result = page.service.scan(root)
    page._on_scan_finished(result)
    plan = [p for p in result.plans if p.files][0]

    page.flow.toggle.setChecked(False)
    assert state.load(paths.gui_state)["show_flow"] is False
    page._handle_event(ev.event(ev.FOLDER_STARTED, folder=plan.folder, files=len(plan.files)))
    page._handle_event(ev.event(ev.FILE_STARTED, folder=plan.folder, index=1))
    assert page.flow.rows == {} and not page.flow.scene.enabled
    window.close()

    qt_app, window, page = open_page(paths)
    assert not page.flow.enabled
    window.close()


def test_empty_leaf_folder_is_flagged_and_parent_folders_are_not(tmp_path, offscreen):
    from curator.modules.xray import status

    root = tree_with_two_folders(tmp_path)
    empty = os.path.join(root, "Пустая")
    os.makedirs(empty)
    qt_app, window, page = open_page(program(tmp_path))
    page._leaves = status.leaf_folders(root)
    page._on_scan_finished(page.service.scan(root))
    assert empty in page.folders.rows and page.empty_folders == [empty]
    row = page.folders.rows[empty]
    assert row.status.text() == status.NO_IMAGES and row.status.property("tone") == status.WARNING
    assert root not in page.folders.rows                      # корень с вложенными папками — не «пустая»
    assert "папок без снимков 1" in page.current_label.text() and page.errors.count == 1
    window.close()


def test_rollback_buttons_only_where_rollback_is_possible_and_rollback_all_is_red(tmp_path, offscreen):
    from curator.modules.xray import status

    qt_app, window, page = open_page(program(tmp_path))
    window.show()
    folder = os.path.join(str(tmp_path), "Объект")
    page.folders.set_status(folder, "готово", status.SUCCESS)
    button = page.folders.rows[folder].button
    assert button.isHidden()
    page.folders.set_rollback_enabled(folder, True)
    assert not button.isHidden() and button.isEnabled()
    page.folders.disable_all_rollback(hide=True)
    assert button.isHidden()
    page._on_task_done({"tag": "rollback_all", "result": None})
    assert page.status_label.property("tone") == status.ERROR
    page._on_task_done({"tag": "scan", "result": None})
    assert page.status_label.property("tone") == status.NEUTRAL
    window.close()


def test_stop_by_user_is_not_an_error(tmp_path, offscreen):
    from curator.app.service import FolderOutcome, SessionSummary
    from curator.modules.xray import status

    qt_app, window, page = open_page(program(tmp_path))
    folder = os.path.join(str(tmp_path), "Объект")
    page._handle_event(ev.event(ev.FOLDER_FINISHED, folder=folder, state=states.FOLDER_CANCELLED,
                                error="x: остановлено пользователем"))
    assert page.errors.count == 0
    assert page.folders.rows[folder].status.text() == "остановлено, папка не изменена"
    summary = SessionSummary(root="/r", outcomes=[FolderOutcome(folder=folder, state=states.FOLDER_CANCELLED)])
    assert summary.failed == [] and len(summary.stopped) == 1
    assert "с ошибками 0" in status.summary_line(summary) and "остановлено папок 1" in status.summary_line(summary)
    window.close()
