#!/usr/bin/env python3
"""Дерево папок для сквозной проверки собранного приложения (этап 8).

    python scripts/e2e_tree.py create DIR      создать снимки
    python scripts/e2e_tree.py check DIR --state processed|original

Проверяет именно то, ради чего всё делается: JPEG появились, оригиналы
уехали в ИСХ без потерь, а после отката папка вернулась в прежний вид.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from tifjpg.imaging.vips_runtime import load_pyvips  # noqa: E402

# Папки с кириллицей, пробелами и № — как на рабочих дисках.
LAYOUT = {
    os.path.join("Объект 001", ""): ["снимок 1.tif", "снимок 2.tif", "снимок 10.tif"],
    os.path.join("Объект 002 №2", "ИСХ"): ["1.tiff", "2.tiff"],
}
HASHES_FILE = "hashes.json"
WIDTH, HEIGHT = 800, 600


def create(root):
    vips = load_pyvips()
    if os.path.isdir(root):
        shutil.rmtree(root)
    hashes = {}
    for relative, names in LAYOUT.items():
        directory = os.path.join(root, relative)
        os.makedirs(directory, exist_ok=True)
        for index, name in enumerate(names):
            path = os.path.join(directory, name)
            ramp = vips.Image.xyz(WIDTH, HEIGHT).extract_band(0)
            ramp = ramp.linear(65535.0 / (WIDTH - 1), index * 137).cast("ushort")
            ramp.copy(interpretation="grey16").tiffsave(path, compression="lzw" if index % 2 else "none")
            hashes[name] = _hash(path)
    with open(os.path.join(root, HASHES_FILE), "w", encoding="utf-8") as stream:
        json.dump(hashes, stream, ensure_ascii=False, indent=1)
    print("создано дерево: {} файлов в {} папках".format(sum(len(v) for v in LAYOUT.values()), len(LAYOUT)))
    return 0


def check(root, state):
    with open(os.path.join(root, HASHES_FILE), encoding="utf-8") as stream:
        hashes = json.load(stream)
    problems = []
    for relative, names in LAYOUT.items():
        folder = os.path.join(root, relative.split(os.sep)[0])
        archive = os.path.join(folder, "ИСХ")
        if state == "original":
            source_dir = os.path.join(root, relative)
            for name in names:
                if not os.path.exists(os.path.join(source_dir, name)):
                    problems.append("{}: файл {} не вернулся на место".format(folder, name))
                elif _hash(os.path.join(source_dir, name)) != hashes[name]:
                    problems.append("{}: файл {} изменился".format(folder, name))
            for name in os.listdir(folder):
                if name.lower().endswith((".jpeg", ".jpg", ".part")):
                    problems.append("{}: остался файл {}".format(folder, name))
            continue

        jpegs = sorted(name for name in os.listdir(folder) if name.lower().endswith(".jpeg"))
        expected = ["Рентгенограмма_{}.jpeg".format(number) for number in range(1, len(names) + 1)]
        if jpegs != sorted(expected):
            problems.append("{}: JPEG {} вместо {}".format(folder, jpegs, sorted(expected)))
        if not os.path.isdir(archive):
            problems.append("{}: нет папки ИСХ".format(folder))
            continue
        archived = sorted(os.listdir(archive))
        expected_tiffs = sorted("Рентгенограмма_{}.tif".format(number) for number in range(1, len(names) + 1))
        if archived != expected_tiffs:
            problems.append("{}: в ИСХ {} вместо {}".format(folder, archived, expected_tiffs))
        stored = {_hash(os.path.join(archive, name)) for name in archived}
        if not {hashes[name] for name in names} <= stored:
            problems.append("{}: оригиналы в ИСХ не совпадают с исходными".format(folder))
        for name in expected:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                image = load_pyvips().Image.new_from_file(path)
                if (image.width, image.height) != (WIDTH, HEIGHT):
                    problems.append("{}: размер {} не совпадает с исходником".format(name, (image.width, image.height)))

    if problems:
        print("ПРОБЛЕМЫ:")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("состояние «{}» подтверждено".format(state))
    return 0


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    commands.required = True
    create_parser = commands.add_parser("create")
    create_parser.add_argument("root")
    check_parser = commands.add_parser("check")
    check_parser.add_argument("root")
    check_parser.add_argument("--state", choices=("processed", "original"), required=True)
    args = parser.parse_args(argv)
    return create(args.root) if args.command == "create" else check(args.root, args.state)


if __name__ == "__main__":
    sys.exit(main())
