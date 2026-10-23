# -*- mode: python ; coding: utf-8 -*-
# Этап 11: smoke-сборка Qt (PyQt5) + libvips для проверки на Windows 7 (ARCHITECTURE.md, 26.3).
#   python -m PyInstaller packaging/qt_smoke.spec --noconfirm --distpath dist --workpath build/pyinstaller
# DLL libvips берутся из CURATOR_VIPS_DIR или build/vips (scripts/fetch_libvips.py).

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VIPS_DIR = os.environ.get("CURATOR_VIPS_DIR") or os.path.join(ROOT, "build", "vips")

vips_binaries = [
    (os.path.join(VIPS_DIR, name), ".")
    for name in sorted(os.listdir(VIPS_DIR))
    if name.lower().endswith(".dll")
]

a = Analysis(
    [os.path.join(SPECPATH, "qt_smoke_entry.py")],
    pathex=[os.path.join(ROOT, "src"), SPECPATH],
    binaries=vips_binaries,
    # Иконки Lucide (ISC) с лицензией — как будут лежать в версии 2.
    datas=[(os.path.join(SPECPATH, "qt_smoke_icons"), "qt_smoke_icons")],
    hiddenimports=["_cffi_backend", "PyQt5.QtSvg"],
    # tkinter в версии 2 не нужен — без него размер сборки ближе к настоящему.
    excludes=["_libvips", "pyvips_binary", "tkinter", "numpy", "PIL", "pytest"],
    noarchive=False,
)


def _is_system_crt(entry):
    """UCRT берём с целевой машины: копии со сборочной (Windows Server) там не запустятся."""
    name = os.path.basename(entry[0]).lower()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


# Не нужны окну на виджетах (~33 МБ): opengl32sw.dll — запасной OpenGL (Mesa) для QtQuick/QOpenGLWidget;
# qwebgl.dll — показ окна в браузере, тянет за собой Quick/Qml/Network/WebSockets.
# Если что-то из этого всё же понадобится, check_pe_imports.py в CI сообщит о недостающей DLL.
UNUSED_QT = {
    "opengl32sw.dll", "qwebgl.dll", "qt5quick.dll", "qt5qml.dll", "qt5qmlmodels.dll", "qt5network.dll",
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
    name="curator_qt_smoke",
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="curator_qt_smoke",
)
