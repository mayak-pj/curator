"""Платформа версии 2 без Qt: реестр модулей (D21, D24), тема из digger, иконки."""

import io
import os
import re

from curator.platform import modules, state
from curator.platform.theme import icons, tokens
from curator.platform.theme.qss import TEMPLATE, build_qss

IMAGES = {"chevron_down": "/tmp/d.svg", "chevron_up": "/tmp/u.svg"}


def test_tiles_are_named_as_decided_in_d24():
    assert [module.title for module in modules.MODULES] == [
        "Рентгенограммы TIFF → JPEG", "Проверка", "Сравнение", "Сортировка", "Выгрузка", "Удаление"]
    assert [module.id for module in modules.MODULES if module.available] == ["xray"]
    assert len({module.id for module in modules.MODULES}) == len(modules.MODULES)
    assert modules.get("xray").available


def test_every_module_icon_exists():
    names = icons.names()
    for module in modules.MODULES:
        assert module.icon in names, module.id
    for needed in ("images", "lightbulb", "lightbulb-off", "arrow-left", "check", "triangle-alert"):
        assert needed in names


def test_themes_share_tokens_and_qss_has_no_own_colours():
    assert set(tokens.LIGHT) == set(tokens.DARK)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", TEMPLATE), "цвета в QSS — только из токенов"
    for name in tokens.THEMES:
        qss = build_qss(tokens.tokens(name), IMAGES)
        assert "$" not in qss and qss.count("{") == qss.count("}")
        assert "QPushButton#tile" in qss


def test_icons_are_lucide_with_license():
    for name in icons.names():
        raw = io.open(os.path.join(icons.ICON_DIR, name + ".svg"), encoding="utf-8").read()
        assert "currentColor" in raw and "lucide" in raw, name
    licence = io.open(os.path.join(icons.ICON_DIR, icons.LICENSE_FILE), encoding="utf-8").read()
    assert "ISC License" in licence and "Lucide" in licence


def test_gui_state_is_shared_with_version_1_format(tmp_path):
    path = str(tmp_path / "gui_state.json")
    assert state.load(path) == {}
    (tmp_path / "gui_state.json").write_text('{"last_root": "Z:\\\\Объект", "dark": false}', encoding="utf-8")
    assert state.save(path, dark=True)
    assert state.load(path) == {"last_root": "Z:\\Объект", "dark": True}
    (tmp_path / "gui_state.json").write_text("испорчен", encoding="utf-8")
    assert state.load(path) == {}
