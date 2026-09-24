import os

from tests.integration.test_scanner import build_tree
from tifjpg.app.scan import as_dict, render_report, scan_root
from tifjpg.domain.models import CONFLICT, DONE


def test_scan_root_summarises_tree(tmp_path):
    root = build_tree(str(tmp_path))
    result = scan_root(root)

    assert result.folders_seen == 8
    assert {os.path.basename(plan.folder) for plan in result.to_process} == {
        "Объект 001", "Объект 002", "вложенная папка"}
    assert result.total_files == 6
    assert [os.path.basename(plan.folder) for plan in result.by_kind(DONE)] == ["Объект 003"]
    assert [os.path.basename(plan.folder) for plan in result.by_kind(CONFLICT)] == ["Объект 004"]
    assert not result.errors


def test_jpegs_are_placed_next_to_archive_not_inside(tmp_path):
    """R4: оригиналы лежат в ИСХ, JPEG должны появиться уровнем выше."""
    root = build_tree(str(tmp_path))
    plan = next(plan for plan in scan_root(root).plans if plan.folder.endswith("Объект 002"))

    for planned in plan.files:
        assert os.path.dirname(planned.jpeg) == plan.folder
        assert os.path.basename(os.path.dirname(planned.jpeg)) == "Объект 002"
        assert "ИСХ" not in planned.jpeg
        assert os.path.dirname(planned.archive) == plan.archive_dir


def test_report_mentions_every_group(tmp_path):
    root = build_tree(str(tmp_path))
    report = render_report(scan_root(root), full=True)

    assert "К ОБРАБОТКЕ (3)" in report
    assert "НЕ ОБРАБАТЫВАЛИСЬ (уже готовы) (1)" in report
    assert "КОНФЛИКТЫ (папка не тронута) (1)" in report
    assert "ИТОГО: к обработке 6 файлов в 3 папках" in report
    assert "будет создана папка ИСХ" in report


def test_scan_changes_nothing_on_disk(tmp_path):
    root = build_tree(str(tmp_path))

    def snapshot_of_tree():
        found = {}
        for directory, _, files in os.walk(root):
            for name in files:
                path = os.path.join(directory, name)
                found[path] = os.stat(path).st_mtime_ns
        return found

    before = snapshot_of_tree()
    scan_root(root, include_empty=True)
    assert snapshot_of_tree() == before


def test_json_report_contains_full_paths(tmp_path):
    root = build_tree(str(tmp_path))
    data = as_dict(scan_root(root))
    folder = next(item for item in data["folders"] if item["path"].endswith("Объект 001"))

    assert folder["rule"] == "R3"
    assert folder["creates_archive_dir"] is True
    assert [os.path.basename(file["jpeg"]) for file in folder["files"]] == [
        "Рентгенограмма_1.jpeg", "Рентгенограмма_2.jpeg", "Рентгенограмма_3.jpeg"]
