"""Стартовое окно версии 2 на Qt без экрана (offscreen): плитки, страница модуля, снимки."""

import json
import os
import sys

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from curator.appdirs import AppPaths


@pytest.fixture
def offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        monkeypatch.setenv("QT_QPA_FONTDIR", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))


def program(tmp_path, config=None):
    base = tmp_path / "Программы" / "Curator v2"
    base.mkdir(parents=True)
    if config is not None:
        (base / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return AppPaths(str(base))


def test_snapshot_mode_saves_both_themes(tmp_path, offscreen, monkeypatch):
    from curator.platform import app

    paths = program(tmp_path)
    monkeypatch.setattr(app.appdirs, "AppPaths", lambda: paths)
    out = tmp_path / "снимки"

    assert app.run(["--snapshot", str(out)]) == 0

    report = (out / app.REPORT_NAME).read_text(encoding="utf-8")
    assert "ИТОГ: ошибок нет" in report and "Плиток: 6 (рабочих 1)" in report
    assert "Русские подписи Qt: да" in report
    for name in ("start_light", "start_dark", "xray_light", "xray_dark"):
        assert (out / (name + ".png")).stat().st_size > 1000
    assert not os.path.exists(paths.gui_state)  # проверка не меняет выбранную пользователем тему


def test_tiles_open_module_and_theme_is_remembered(tmp_path, offscreen):
    from curator.platform import app, state
    from curator.platform.start_window import StartWindow

    paths = program(tmp_path)
    qt_app, context, _ = app._create_app(paths)
    window = StartWindow(context)
    tiles = window.start_page.tiles
    assert [tile.isEnabled() for tile in tiles] == [True] + [False] * 5

    tiles[1].click()  # заглушка не открывается
    assert window.stack.currentWidget() is window.start_page
    tiles[0].click()
    assert window.stack.currentWidget() is window.module_page
    assert window.title_label.text() == "Рентгенограммы TIFF → JPEG"
    window.back_button.click()
    assert window.stack.currentWidget() is window.start_page

    window.theme_button.click()
    assert context.theme.name == "dark" and state.load(paths.gui_state)["dark"] is True
    window.theme_button.click()
    assert state.load(paths.gui_state)["dark"] is False
    window.close()


def test_xray_page_reports_readiness_next_to_version_1(tmp_path):
    from curator.modules.xray.page import readiness

    paths = program(tmp_path, {"locks_dir": os.path.join("..", "Curator", "locks"),
                               "legacy_journal_dirs": [os.path.join("..", "Curator", "journal")]})
    ready, lines = readiness(paths)
    assert not ready and "папка блокировок версии 1 не найдена" in lines[0]

    for name in ("locks", "journal"):
        (tmp_path / "Программы" / "Curator" / name).mkdir(parents=True)
    ready, lines = readiness(paths)
    assert ready
    assert lines[0].endswith(os.path.join("Программы", "Curator", "locks"))
    assert os.listdir(str(tmp_path / "Программы" / "Curator" / "locks")) == []  # проверка ничего не пишет
