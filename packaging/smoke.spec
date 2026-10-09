# -*- mode: python ; coding: utf-8 -*-
# Этап 2: smoke-сборка для проверки стека на Windows 7.
#   python -m PyInstaller packaging/smoke.spec --noconfirm --distpath dist --workpath build/pyinstaller
# DLL libvips берутся из CURATOR_VIPS_DIR или build/vips (scripts/fetch_libvips.py).

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VIPS_DIR = os.environ.get("CURATOR_VIPS_DIR") or os.path.join(ROOT, "build", "vips")
# DLL кладутся в корень _internal: PyInstaller и так собирает туда зависимости,
# отдельная подпапка приводила к дублированию всех DLL.
vips_binaries = [
    (os.path.join(VIPS_DIR, name), ".")
    for name in sorted(os.listdir(VIPS_DIR))
    if name.lower().endswith(".dll")
]

a = Analysis(
    [os.path.join(SPECPATH, "smoke_entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=vips_binaries,
    hiddenimports=["_cffi_backend"],
    # pyvips на Windows работает в ABI-режиме с нашими DLL; pyvips-binary несовместим с Win7.
    excludes=["_libvips", "pyvips_binary"],
    noarchive=False,
)

# PyInstaller копирует UCRT (ucrtbase.dll, api-ms-win-*.dll) из системы сборочной машины
# (Windows Server 2022, subsystem 10.0). На Windows 7 такие копии рядом с exe перекрыли бы
# системный UCRT (KB2999226) — исключаем их, используется UCRT целевого ПК.
def _is_system_crt(entry):
    name = os.path.basename(entry[0]).lower()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


a.binaries = [entry for entry in a.binaries if not _is_system_crt(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="curator_smoke",
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="curator_smoke",
)
