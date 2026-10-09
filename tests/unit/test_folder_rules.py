from curator.domain.folder_rules import classify
from curator.domain.models import CONFLICT, DONE, EMPTY, PROCESS_FROM_ARCHIVE, PROCESS_ROOT, FolderSnapshot
from curator.domain.naming import XrayNaming

naming = XrayNaming()


def snapshot(files=(), archive_dir=None, archive_files=()):
    return FolderSnapshot(path=r"C:\work\Объект 1", files=tuple(files),
                          archive_dir=archive_dir, archive_files=tuple(archive_files))


def test_r3_tiffs_in_folder():
    decision = classify(snapshot(["1.tif", "2.TIFF", "заметка.txt"]), naming)
    assert (decision.kind, decision.rule) == (PROCESS_ROOT, "R3")


def test_r4_tiffs_only_in_archive():
    decision = classify(snapshot(archive_dir="Исх", archive_files=["1.tiff", "2.tiff"]), naming)
    assert (decision.kind, decision.rule) == (PROCESS_FROM_ARCHIVE, "R4")


def test_r1_counts_match_after_manual_processing():
    decision = classify(snapshot(
        files=["Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"],
        archive_dir="ИСХ", archive_files=["1.tif", "2.tif"]), naming)
    assert (decision.kind, decision.rule) == (DONE, "R1")


def test_r1_when_originals_still_next_to_jpegs():
    decision = classify(snapshot(files=["1.tif", "2.tif", "рентгенограмма_1.jpg", "рентгенограмма_2.jpg"]), naming)
    assert decision.kind == DONE


def test_r2_counts_do_not_match():
    decision = classify(snapshot(files=["1.tif", "2.tif", "3.tif", "Рентгенограмма 1.jpeg"]), naming)
    assert (decision.kind, decision.rule) == (CONFLICT, "R2")
    assert "не совпадает" in decision.reason


def test_r5_tiffs_both_in_folder_and_archive():
    decision = classify(snapshot(files=["1.tif"], archive_dir="ИСХ", archive_files=["0.tif"]), naming)
    assert (decision.kind, decision.rule) == (CONFLICT, "R5")


def test_r6_nothing_to_do():
    assert classify(snapshot(files=["readme.txt"]), naming).kind == EMPTY


def test_jpegs_without_tiffs_are_done_not_conflict():
    assert classify(snapshot(files=["Рентгенограмма 1.jpeg"]), naming).kind == DONE


def test_foreign_jpegs_do_not_matter():
    decision = classify(snapshot(files=["1.tif", "схема.jpg", "фото.jpeg"]), naming)
    assert decision.kind == PROCESS_ROOT
