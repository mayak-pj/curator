"""Реестр модулей стартового окна (ARCHITECTURE.md 26.1, решения D21 и D24).

Модуль — это плитка: id, название, описание, иконка и функция, которая строит страницу модуля.
Заглушка — модуль без функции открытия: плитка видна, но не нажимается. Новый модуль
добавляется одной записью в MODULES; стартовое окно о конкретных модулях ничего не знает.
"""

from dataclasses import dataclass
from typing import Callable, Optional

STUB_TEXT = "Модуль в разработке."


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    description: str
    icon: str                                   # имя иконки Lucide из platform/assets/icons
    open: Optional[Callable] = None             # open(context) -> QWidget; context — platform.app.Context

    @property
    def available(self):
        return self.open is not None


def _open_xray(context):
    from curator.modules.xray.page import XrayPage

    return XrayPage(context)


MODULES = (
    Module("xray", "Рентгенограммы TIFF → JPEG",
           "Переводит снимки TIFF в JPEG, оригиналы переносит в папку ИСХ. Журнал и откат по каждой папке.",
           "images", _open_xray),
    Module("check", "Проверка", STUB_TEXT, "file-check"),
    Module("compare", "Сравнение", STUB_TEXT, "git-compare"),
    Module("sort", "Сортировка", STUB_TEXT, "folder-tree"),
    Module("export", "Выгрузка", STUB_TEXT, "upload"),
    Module("delete", "Удаление", STUB_TEXT, "trash-2"),
)


def get(module_id):
    for module in MODULES:
        if module.id == module_id:
            return module
    raise KeyError("нет модуля {!r}".format(module_id))
