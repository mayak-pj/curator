import os

from curator.domain.folder_rules import classify
from curator.domain.models import PROCESS_ROOT, FolderDecision, FolderSnapshot
from curator.domain.naming import XrayNaming
from curator.transaction.planner import plan_folder

naming = XrayNaming()
FOLDER = os.path.join("C:" + os.sep, "work", "Объект 1")


def make_plan(files=(), archive_dir=None, archive_files=(), max_path=259):
    snapshot = FolderSnapshot(path=FOLDER, files=tuple(files),
                              archive_dir=archive_dir, archive_files=tuple(archive_files))
    return plan_folder(snapshot, classify(snapshot, naming), naming, max_path=max_path)


def names(plan, attribute):
    return [os.path.basename(getattr(planned, attribute)) for planned in plan.files]


def test_numbering_follows_natural_order():
    plan = make_plan(["снимок 10.tif", "снимок 2.tif", "снимок 1.tif"])
    assert names(plan, "source") == ["снимок 1.tif", "снимок 2.tif", "снимок 10.tif"]
    assert names(plan, "jpeg") == ["Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg", "Рентгенограмма 3.jpeg"]
    assert names(plan, "archive") == ["Рентгенограмма 1.tif", "Рентгенограмма 2.tif", "Рентгенограмма 3.tif"]
    assert plan.ok and plan.creates_archive_dir


def test_archive_dir_keeps_existing_spelling():
    plan = make_plan(["1.tif"], archive_dir="Исх")
    assert os.path.basename(plan.archive_dir) == "Исх"
    assert not plan.creates_archive_dir


def test_new_archive_dir_is_uppercase():
    assert os.path.basename(make_plan(["1.tif"]).archive_dir) == "ИСХ"


def test_tiff_extension_is_normalised():
    plan = make_plan(["снимок.TIFF"])
    assert names(plan, "archive") == ["Рентгенограмма.tif"]


def test_jpegs_go_next_to_archive_not_into_it():
    plan = make_plan(archive_dir="ИСХ", archive_files=["a.tiff", "b.tiff"])
    assert all(os.path.dirname(planned.jpeg) == FOLDER for planned in plan.files)
    assert all(os.path.dirname(planned.archive) == plan.archive_dir for planned in plan.files)
    assert all(planned.source_in_archive for planned in plan.files)


def test_reports_two_step_rename_inside_archive():
    plan = make_plan(archive_dir="ИСХ", archive_files=["Рентгенограмма 2.tif", "снимок.tif"])
    assert plan.two_step_renames
    assert not plan.problems


def test_no_two_step_when_names_already_correct():
    plan = make_plan(archive_dir="ИСХ", archive_files=["Рентгенограмма 1.tif", "Рентгенограмма 2.tif"])
    assert not plan.two_step_renames
    assert all(planned.archive_unchanged for planned in plan.files)


def test_existing_target_jpeg_blocks_folder():
    plan = make_plan(["1.tif", "2.tif", "схема.jpg", "Рентгенограмма 1.jpeg".upper()])
    # JPEG по шаблону есть, но количество не совпадает -> конфликт, план пустой
    assert not plan.decision.processable
    assert not plan.files


def test_leftover_part_file_is_a_problem():
    plan = make_plan(["1.tif", "Рентгенограмма.jpeg.part"])
    assert any("временный файл" in problem for problem in plan.problems)
    assert not plan.ok


def test_tiffs_in_folder_and_archive_are_a_conflict():
    # До проверки занятых имён дело не доходит: такую папку правила не отдают в обработку.
    plan = make_plan(["1.tif"], archive_dir="ИСХ", archive_files=["Рентгенограмма 1.tif"])
    assert plan.decision.rule == "R5"
    assert not plan.files


def test_occupied_name_in_archive_is_a_problem():
    # Защита планировщика: решение приходит извне, а целевое имя в архиве занято.
    snapshot = FolderSnapshot(path=FOLDER, files=("1.tif",), archive_dir="ИСХ",
                              archive_files=("Рентгенограмма.tif",))
    decision = FolderDecision(PROCESS_ROOT, "R3", "передано вручную")
    plan = plan_folder(snapshot, decision, naming)
    assert any("в архиве уже есть" in problem for problem in plan.problems)
    assert not plan.ok


def test_long_path_is_refused():
    plan = make_plan(["1.tif"], max_path=len(FOLDER) + 5)
    assert any("путь длиннее" in problem for problem in plan.problems)
    assert not plan.ok


def test_plan_is_empty_for_non_processable_folders():
    plan = make_plan(["readme.txt"])
    assert plan.files == () and not plan.ok


# --- D26: готовая папка, «рентгенограмма….jpg» -> «….jpeg» ------------------------------

def test_done_folder_plans_jpg_to_jpeg_keeping_the_name():
    plan = make_plan(["Рентгенограмма 1.jpg", "рентгенограмма-2.JPG", "фото.jpg"],
                     archive_dir="ИСХ", archive_files=["a.tif", "b.tif"])
    assert plan.decision.kind == "done"
    assert [(os.path.basename(old), os.path.basename(new)) for old, new in plan.jpeg_renames] == [
        ("Рентгенограмма 1.jpg", "Рентгенограмма 1.jpeg"),
        ("рентгенограмма-2.JPG", "рентгенограмма-2.jpeg"),
    ]
    assert plan.renames_ok and not plan.ok and not plan.files


def test_done_folder_with_jpeg_already_needs_nothing():
    plan = make_plan(["Рентгенограмма 1.jpeg"], archive_dir="ИСХ", archive_files=["a.tif"])
    assert plan.jpeg_renames == () and not plan.renames_ok


def test_occupied_jpeg_name_leaves_the_folder_untouched():
    plan = make_plan(["Рентгенограмма 1.jpg", "Рентгенограмма 2.jpg", "Рентгенограмма 2.jpeg"],
                     archive_dir="ИСХ", archive_files=["a.tif", "b.tif", "c.tif"])
    assert plan.decision.kind == "done"
    assert plan.jpeg_renames == () and not plan.renames_ok
    assert "Рентгенограмма 2.jpeg уже существует" in plan.problems[0]


def test_conflict_folder_renames_jpg_but_leaves_tiffs():
    # Папка частично обработана руками: 2 JPG, 1 TIFF — .jpg приводим к .jpeg, TIFF не трогаем.
    plan = make_plan(["Рентгенограмма 1.jpg", "Рентгенограмма 2.jpg"], archive_dir="ИСХ", archive_files=["a.tif"])
    assert plan.decision.kind == "conflict"
    assert [os.path.basename(new) for _, new in plan.jpeg_renames] == [
        "Рентгенограмма 1.jpeg", "Рентгенограмма 2.jpeg"]
    assert plan.renames_ok and not plan.ok and not plan.files


def test_appledouble_jpg_is_not_a_processed_jpeg():
    plan = make_plan(["Рентгенограмма 1.jpg", "._Рентгенограмма 1.jpg"], archive_dir="ИСХ", archive_files=["a.tif"])
    assert plan.decision.kind == "done"
    assert [os.path.basename(old) for old, _ in plan.jpeg_renames] == ["Рентгенограмма 1.jpg"]
