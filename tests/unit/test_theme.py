"""Палитра тёмной/светлой темы — чистая логика, без создания окна (ARCHITECTURE.md, 18)."""

from curator.gui import theme


def teardown_function(_test):
    theme.set_theme(False)  # переключение темы — общее состояние модуля


def test_light_is_the_default():
    theme.set_theme(False)
    assert not theme.is_dark()
    assert theme.background() == theme.LIGHT["background"]
    assert theme.colour("running") == theme.LIGHT["colours"]["running"]


def test_switching_to_dark_changes_every_accessor():
    theme.set_theme(True)
    assert theme.is_dark()
    assert theme.background() == theme.DARK["background"]
    assert theme.surface() == theme.DARK["surface"]
    assert theme.border() == theme.DARK["border"]
    assert theme.text() == theme.DARK["text"]
    assert theme.muted() == theme.DARK["muted"]
    for key in theme.DARK["colours"]:
        assert theme.colour(key) == theme.DARK["colours"][key]


def test_button_text_is_black_on_light_and_white_on_dark():
    # Пользователь явно просил ровно это (01.10.2026, запуск на Mac):
    # один и тот же цвет текста кнопок в каждой теме, без разнобоя.
    theme.set_theme(False)
    assert theme.button_text() == "#000000"
    theme.set_theme(True)
    assert theme.button_text() == "#ffffff"


def test_unknown_colour_key_falls_back_to_text():
    theme.set_theme(False)
    assert theme.colour("no-such-key") == theme.text()
