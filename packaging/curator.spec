# -*- mode: python ; coding: utf-8 -*-
# Итоговая сборка приложения (ARCHITECTURE.md, раздел 21).
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

a = Analysis(
    [os.path.join(ROOT, "src", "curator", "__main__.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=vips_binaries,
    datas=vips_datas,
    hiddenimports=["_cffi_backend"],
    # pyvips на Windows работает в ABI-режиме с нашими DLL; pyvips-binary несовместим с Win7.
    excludes=["_libvips", "pyvips_binary", "numpy", "PIL", "pytest"],
    noarchive=False,
)


def _is_system_crt(entry):
    """UCRT берём с целевой машины: копии со сборочной (Windows Server) там не запустятся."""
    name = os.path.basename(entry[0]).lower()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


a.binaries = [entry for entry in a.binaries if not _is_system_crt(entry)]

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
