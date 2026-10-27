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

    assert page.folders.rows[broken].status.text() == "ошибка, откат выполнен: сеть"
    assert not page.folders.rows[broken].button.isEnabled() and page.processed_folders == []
    assert page.errors.text().count("нет места") == 1
    assert not page.accept_button.isEnabled()
    window.close()
