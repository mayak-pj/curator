"""Окно создать в CI нельзя, но модули должны импортироваться без ошибок."""

import pytest

pytest.importorskip("tkinter")


def test_gui_modules_import():
    from tifjpg.gui import error_panel, folder_list, main_window, recovery_dialog, theme

    assert hasattr(main_window, "MainWindow")
    assert hasattr(folder_list, "FolderList")
    assert hasattr(error_panel, "ErrorPanel")
    assert hasattr(recovery_dialog, "ask")
    assert theme.colour("success") != theme.colour("error")


def test_entry_point_routes_arguments():
    from tifjpg.app.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["scan", "/tmp"]).command == "scan"
    assert parser.parse_args(["process", "/tmp"]).command == "process"
