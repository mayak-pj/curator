# -*- mode: python ; coding: utf-8 -*-
# Итоговая сборка приложения (ARCHITECTURE.md, раздел 21). В main — версия 2: окно на PyQt5
# (раздел 26, отсев лишнего Qt — как в qt_smoke.spec этапа 11); версия 1 (tkinter) — ветка v1.
#   python -m PyInstaller packaging/curator.spec --noconfirm --distpath dist --workpath build/pyinstaller
# DLL libvips берутся из CURATOR_VIPS_DIR или build/vips (scripts/fetch_libvips.py).

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VIPS_DIR = os.environ.get("CURATOR_VIPS_DIR") or os.path.join(ROOT, "build", "vips")

vips_binaries = [
    (os.path.join(VIPS_DIR, name), ".")
    for name in sorted(os.listdir(VIPS_DIR))
    if name.lower().endswith(".dll")
]
# Лицензия libvips и список версий его компонентов едут рядом с программой.
vips_datas = [
    (os.path.join(VIPS_DIR, name), "licenses")
    for name in ("libvips-LICENSE.txt", "libvips-versions.json")
    if os.path.exists(os.path.join(VIPS_DIR, name))
]

# Иконки Lucide (ISC) с лицензией: icons.py ищет их рядом с пакетом curator.platform.
icon_datas = [(os.path.join(ROOT, "src", "curator", "platform", "assets", "icons"),
               os.path.join("curator", "platform", "assets", "icons"))]

a = Analysis(
    [os.path.join(ROOT, "src", "curator", "__main__.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=vips_binaries,
    datas=vips_datas + icon_datas,
    hiddenimports=["_cffi_backend", "PyQt5.QtSvg"],
    # pyvips на Windows работает в ABI-режиме с нашими DLL; pyvips-binary несовместим с Win7.
    # Окно версии 1 (tkinter) из exe версии 2 не открывается — его в сборке нет.
    excludes=["_libvips", "pyvips_binary", "tkinter", "numpy", "PIL", "pytest"],
    noarchive=False,
)


def _is_system_crt(entry):
    """UCRT берём с целевой машины: копии со сборочной (Windows Server) там не запустятся."""
    name = os.path.basename(entry[0]).lower()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


# Не нужны окну на виджетах (~33 МБ), подробности — qt_smoke.spec (этап 11). Если что-то из
# этого всё же понадобится, check_pe_imports.py в CI сообщит о недостающей DLL.
UNUSED_QT = {
    "opengl32sw.dll", "qwebgl.dll", "qtuiotouchplugin.dll", "qt5quick.dll", "qt5qml.dll", "qt5qmlmodels.dll", "qt5network.dll",
    "qt5websockets.dll",
}


def _is_unused_qt(entry):
    return os.path.basename(entry[0]).lower() in UNUSED_QT


def _is_foreign_translation(entry):
    """Из переводов Qt нужны только русские (подписи стандартных кнопок)."""
    path = entry[0].replace("\\", "/")
    return "/translations/" in path and path.endswith(".qm") and "_ru." not in os.path.basename(path)


a.binaries = [entry for entry in a.binaries if not (_is_system_crt(entry) or _is_unused_qt(entry))]
a.datas = [entry for entry in a.datas if not _is_foreign_translation(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="curator",
    console=False,
    upx=False,
    version=os.path.join(SPECPATH, "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="curator",
)
