"""Запуск Qt в сборке PyInstaller на Windows 7 (уроки этапа 11, ARCHITECTURE.md 26.3).

Пути к плагинам и переводам Qt — только от пакета PyQt5, никаких путей из QLibraryInfo:
из папки с русскими буквами Qt сам не находит плагины, а TranslationsPath приходит с «?»
вместо букв.
"""

import os


def _qt5_dir():
    import PyQt5

    return os.path.join(os.path.dirname(os.path.abspath(PyQt5.__file__)), "Qt5")


def plugins_dir():
    """Папка плагинов Qt внутри пакета PyQt5 (пустая строка, если её нет)."""
    path = os.path.join(_qt5_dir(), "plugins")
    return path if os.path.isdir(os.path.join(path, "platforms")) else ""


def translations_dir():
    return os.path.join(_qt5_dir(), "translations")


def prepare():
    """До создания QApplication. Возвращает папку плагинов (пустая строка — не найдена)."""
    from PyQt5.QtCore import QCoreApplication, Qt

    plugins = plugins_dir()
    if plugins:
        QCoreApplication.addLibraryPath(plugins)
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    return plugins


def install_russian(app):
    """Русские подписи стандартных кнопок и диалогов Qt. False — перевод не найден (окно работает и так)."""
    from PyQt5.QtCore import QLocale, QTranslator

    translator = QTranslator(app)
    if not translator.load(QLocale("ru_RU"), "qtbase", "_", translations_dir()):
        return False
    app.installTranslator(translator)
    app._curator_translator = translator  # иначе сборщик мусора заберёт перевод
    return True
