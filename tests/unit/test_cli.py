"""Консольные команды в оконной сборке (console=False)."""

import sys


def test_cli_error_without_stderr_returns_code(monkeypatch, tmp_path):
    # Оконная сборка запускается без консоли: sys.stderr = None, ошибка не должна ронять exe.
    from curator.app.cli import main

    monkeypatch.setattr(sys, "stderr", None)
    assert main(["convert", str(tmp_path / "нет.tif"), str(tmp_path / "out.jpeg")]) == 2
