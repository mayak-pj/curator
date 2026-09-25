#!/usr/bin/env python3
"""Скачать закреплённую сборку libvips для Windows x64 и распаковать её DLL.

    python scripts/fetch_libvips.py --dest build/vips

Версия выбрана проверкой импортов на совместимость с Windows 7
(ARCHITECTURE.md, 21.1). Не обновлять без повторной проверки
scripts/check_pe_imports.py: современные тулчейны (Rust, llvm-mingw)
Windows 7 уже не поддерживают.
"""

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
import zipfile

LIBVIPS_VERSION = "8.15.1"
ARCHIVE_NAME = "vips-dev-w64-web-{}.zip".format(LIBVIPS_VERSION)
URL = "https://github.com/libvips/build-win64-mxe/releases/download/v{}/{}".format(LIBVIPS_VERSION, ARCHIVE_NAME)
SHA256 = "bceac3d55e7bb7e3e8aba51073c6cedccd5f6466dfd6d3df5ded6075d2b6e32e"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    archive = os.path.join(cache_dir, ARCHIVE_NAME)
    if os.path.exists(archive) and sha256_of(archive) == SHA256:
        print("Архив уже скачан: {}".format(archive))
        return archive
    partial = archive + ".part"
    print("Скачивание {}".format(URL))
    with urllib.request.urlopen(URL, timeout=120) as response, open(partial, "wb") as stream:
        shutil.copyfileobj(response, stream, 1024 * 1024)
    actual = sha256_of(partial)
    if actual != SHA256:
        os.remove(partial)
        raise SystemExit("Контрольная сумма не совпала: ожидалось {}, получено {}".format(SHA256, actual))
    os.replace(partial, archive)
    return archive


# Из архива берём не только DLL: лицензия и список версий компонентов едут в сборку.
EXTRA_FILES = {"LICENSE": "libvips-LICENSE.txt", "versions.json": "libvips-versions.json"}


def extract_dlls(archive, dest):
    os.makedirs(dest, exist_ok=True)
    for name in os.listdir(dest):
        if name.lower().endswith(".dll"):
            os.remove(os.path.join(dest, name))
    count = 0
    extras = []
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            parts = member.filename.split("/")
            if len(parts) == 3 and parts[1] == "bin" and parts[2].lower().endswith(".dll"):
                _write(bundle, member, os.path.join(dest, parts[2]))
                count += 1
            elif len(parts) == 2 and parts[1] in EXTRA_FILES:
                _write(bundle, member, os.path.join(dest, EXTRA_FILES[parts[1]]))
                extras.append(EXTRA_FILES[parts[1]])
    if not count:
        raise SystemExit("В архиве не найдено DLL")
    print("libvips {}: распаковано {} DLL в {} (+ {})".format(
        LIBVIPS_VERSION, count, dest, ", ".join(sorted(extras)) or "без лицензии"))


def _write(bundle, member, target):
    with bundle.open(member) as source, open(target, "wb") as stream:
        shutil.copyfileobj(source, stream)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=os.path.join("build", "vips"))
    parser.add_argument("--cache", default=os.path.join("build", "downloads"))
    args = parser.parse_args(argv)
    extract_dlls(download(args.cache), args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
