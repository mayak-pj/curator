import pytest

from curator.domain.naming import XrayNaming, normalize
from curator.domain.ordering import natural_sorted

naming = XrayNaming()


def test_generated_names():
    assert naming.jpeg_name(1, total=3) == "Рентгенограмма 1.jpeg"
    assert naming.source_name(12, total=12) == "Рентгенограмма 12.tif"
    assert naming.archive_dir_name() == "ИСХ"


def test_single_file_folder_gets_no_number():
    # my_reports, 28.09.2026: единственный снимок в папке не нумеруется.
    assert naming.jpeg_name(1, total=1) == "Рентгенограмма.jpeg"
    assert naming.source_name(1, total=1) == "Рентгенограмма.tif"


@pytest.mark.parametrize("filename", [
    "Рентгенограмма 1.jpeg",
    "Рентгенограмма.jpeg",       # единственный снимок в папке, без номера
    "Рентгенограмма 1.jpeg",     # как называл человек раньше
    "рентгенограмма-3.jpg",
    "РЕНТГЕНОГРАММА_5.JPEG",
    "Рентгенограмма 7 (копия).jpg",
    "объект Рентгенограмма 2.jpeg",
])
def test_recognises_processed_jpegs(filename):
    assert naming.is_processed_jpeg(filename)


@pytest.mark.parametrize("filename", [
    "снимок 1.jpeg",
    "Рентгенограмма 1.tif",      # не JPEG
    "Рентгенограмма 1.jpeg.part",
    "schema.jpg",
])
def test_ignores_other_files(filename):
    assert not naming.is_processed_jpeg(filename)


def test_yo_and_case_are_ignored():
    assert normalize("РентгЕнограмма") == normalize("рентгенограмма")
    assert naming.is_archive_dir("Исх") and naming.is_archive_dir("ИСХ") and naming.is_archive_dir("исх")
    assert not naming.is_archive_dir("ИСХОДНИКИ")


def test_numbers_sort_as_numbers():
    names = ["снимок 10.tif", "снимок 2.tif", "снимок 1.tif", "Снимок 3.TIF"]
    assert natural_sorted(names) == ["снимок 1.tif", "снимок 2.tif", "Снимок 3.TIF", "снимок 10.tif"]


def test_sorting_is_deterministic_for_similar_names():
    names = ["a1.tif", "A1.tif", "a01.tif"]
    assert natural_sorted(names) == natural_sorted(list(reversed(names)))
