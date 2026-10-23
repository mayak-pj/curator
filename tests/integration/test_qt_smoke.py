"""Проверки smoke-сборки Qt (этап 11) из исходников: Qt без экрана, libvips, снимки окна."""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PyQt5.QtWidgets")
pytestmark = pytest.mark.vips

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


def test_qt_smoke_no_gui(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        monkeypatch.setenv("QT_QPA_FONTDIR", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))
    monkeypatch.syspath_prepend(str(PACKAGING))
    import smoke_entry
    import qt_smoke_entry

    # Отчёт и снимки — во временную папку, а не в packaging/.
    for module in (smoke_entry, qt_smoke_entry):
        monkeypatch.setattr(module, "app_dir", lambda: str(tmp_path))
        monkeypatch.setattr(module, "work_dir", lambda: str(tmp_path))

    assert qt_smoke_entry.main(["--no-gui"]) == 0

    report = (tmp_path / qt_smoke_entry.REPORT_NAME).read_text(encoding="utf-8")
    assert "ИТОГ: ошибок нет" in report
    assert "[OK  ] SVG через QIcon" in report
    for name in qt_smoke_entry.SNAPSHOT_NAMES.values():
        assert (tmp_path / name).stat().st_size > 1000
