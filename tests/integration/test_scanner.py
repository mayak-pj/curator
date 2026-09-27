import os
import sys

import pytest

import tifjpg.fs.scanner as scanner_module
from tifjpg.domain.folder_rules import classify
from tifjpg.domain.models import CONFLICT, DONE, EMPTY, PROCESS_FROM_ARCHIVE, PROCESS_ROOT
from tifjpg.domain.naming import XrayNaming
from tifjpg.fs.scanner import read_folder, scan_tree

naming = XrayNaming()


def build_tree(root):
    """Дерево, повторяющее рабочие диски пользователя."""
    layout = {
        "Объект 001": ["снимок 1.tif", "снимок 2.tif", "снимок 10.tif"],
        "Объект 002/ИСХ": ["1.tiff", "2.tiff"],
        "Объект 003": ["Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"],
        "Объект 003/Исх": ["1.tif", "2.tif"],
        "Объект 004": ["1.tif", "2.tif", "3.tif", "Рентгенограмма 1.jpeg"],
        "Объект 005": ["заметки.txt"],
        "Объект 006/вложенная папка": ["a.TIF"],
    }
    for relative, files in layout.items():
        directory = os.path.join(root, *relative.split("/"))
        os.makedirs(directory, exist_ok=True)
        for name in files:
            with open(os.path.join(directory, name), "wb") as stream:
                stream.write(b"x")
    return root


def decisions(root):
    result = {}
    for snapshot in scan_tree(root, naming):
        relative = os.path.relpath(snapshot.path, root)
        result[relative.replace(os.sep, "/")] = classify(snapshot, naming)
    return result


def test_scan_of_realistic_tree(tmp_path):
    root = build_tree(str(tmp_path))
    found = decisions(root)

    assert found["Объект 001"].kind == PROCESS_ROOT
    assert found["Объект 002"].kind == PROCESS_FROM_ARCHIVE
    assert found["Объект 003"].kind == DONE
    assert found["Объект 004"].kind == CONFLICT
    assert found["Объект 005"].kind == EMPTY
    assert found["Объект 006/вложенная папка"].kind == PROCESS_ROOT


def test_archive_folders_are_not_scanned_separately(tmp_path):
    root = build_tree(str(tmp_path))
    paths = [os.path.relpath(snapshot.path, root) for snapshot in scan_tree(root, naming)]
    assert not any("ИСХ" in path or "Исх" in path for path in paths)


def test_snapshot_sees_archive_content(tmp_path):
    root = build_tree(str(tmp_path))
    snapshot = read_folder(os.path.join(root, "Объект 002"), naming)
    assert snapshot.archive_dir == "ИСХ"
    assert snapshot.archive_tiffs() == ("1.tiff", "2.tiff")
    assert snapshot.tiffs() == ()
    assert snapshot.tiff_count() == 2


def test_files_are_sorted_naturally(tmp_path):
    root = build_tree(str(tmp_path))
    snapshot = read_folder(os.path.join(root, "Объект 001"), naming)
    assert snapshot.tiffs() == ("снимок 1.tif", "снимок 2.tif", "снимок 10.tif")


def test_unreadable_archive_is_reported_not_raised(tmp_path, monkeypatch):
    """Обрыв сети или отказ в доступе не должен прекращать обход дерева."""
    root = build_tree(str(tmp_path))
    real_scandir = os.scandir

    def failing_scandir(path):
        if naming.is_archive_dir(os.path.basename(str(path))):
            raise OSError(5, "доступ запрещён", str(path))
        return real_scandir(path)

    monkeypatch.setattr(scanner_module.os, "scandir", failing_scandir)
    errors = []
    snapshots = scan_tree(root, naming, on_error=lambda path, exc: errors.append((path, exc)))

    assert len(errors) == 2  # две папки архива в дереве
    by_path = {os.path.relpath(snapshot.path, root): snapshot for snapshot in snapshots}
    assert by_path["Объект 001"].tiffs()  # остальные папки прочитаны
    assert by_path["Объект 002"].archive_files == ()


@pytest.mark.skipif(sys.platform == "win32", reason="права доступа проверяются только на POSIX")
def test_unreadable_folder_permissions_are_reported(tmp_path):
    root = build_tree(str(tmp_path))
    locked = os.path.join(root, "Объект 007")
    os.makedirs(locked)
    os.chmod(locked, 0o000)
    errors = []
    try:
        snapshots = scan_tree(root, naming, on_error=lambda path, exc: errors.append((path, exc)))
    finally:
        os.chmod(locked, 0o755)
    assert snapshots
    assert errors or os.geteuid() == 0
