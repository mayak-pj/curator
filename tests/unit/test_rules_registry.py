import pytest

from tifjpg.domain import folder_rules
from tifjpg.domain.folder_rules import DEFAULT_RULES, FolderFacts, Rule, make_classifier
from tifjpg.domain.models import CONFLICT, DONE, PROCESS_ROOT, FolderSnapshot
from tifjpg.domain.naming import XrayNaming

naming = XrayNaming()


def snapshot(files=(), archive_dir=None, archive_files=()):
    return FolderSnapshot(path="/work/Объект", files=tuple(files),
                          archive_dir=archive_dir, archive_files=tuple(archive_files))


def test_default_rule_set_is_registered():
    assert folder_rules.DEFAULT_NAME in folder_rules.available()
    assert folder_rules.get() is folder_rules.get(folder_rules.DEFAULT_NAME)


def test_unknown_rule_set_is_reported():
    with pytest.raises(KeyError) as error:
        folder_rules.get("не существует")
    assert "известные" in str(error.value)


def test_rule_set_can_be_replaced_without_touching_the_core():
    folder_rules.register("test_v2", (
        Rule("X1", CONFLICT, lambda facts: facts.jpegs > 0, lambda facts: "свои правила: есть JPEG"),
        Rule("X2", PROCESS_ROOT, lambda facts: True, lambda facts: "свои правила: обрабатываем всегда"),
    ))
    classify = folder_rules.get("test_v2")

    processed = snapshot(files=["1.tif", "Рентгенограмма_1.jpeg"])
    assert classify(processed, naming).rule == "X1"
    assert classify(snapshot(files=["readme.txt"]), naming).rule == "X2"
    # Набор по умолчанию не изменился.
    assert folder_rules.classify(processed, naming).rule == "R1"


def test_order_of_rules_decides():
    reversed_rules = tuple(reversed(DEFAULT_RULES))
    classify = make_classifier(reversed_rules)
    # Последним в наборе по умолчанию идёт «ничего подходящего», теперь оно первое.
    assert classify(snapshot(files=["1.tif"]), naming).rule == "R6"


def test_rule_set_must_cover_every_case():
    classify = make_classifier((Rule("X", DONE, lambda facts: False, lambda facts: ""),))
    with pytest.raises(RuntimeError):
        classify(snapshot(files=["1.tif"]), naming)


def test_facts_are_counted_case_insensitively():
    facts = FolderFacts.of(snapshot(
        files=["a.TIF", "b.tiff", "РЕНТГЕНОГРАММА 1.JPG", "схема.jpg"],
        archive_dir="Исх", archive_files=["c.tif"]), naming)
    assert (facts.tiffs_in_folder, facts.tiffs_in_archive, facts.total_tiffs, facts.jpegs) == (2, 1, 3, 1)
    assert facts.archive_dir == "Исх"
