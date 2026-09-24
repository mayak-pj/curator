"""Правила «папка готова / папку обрабатывать» (ARCHITECTURE.md, раздел 6).

Правила меняются со временем, поэтому они описаны таблицей, а не кодом:
каждое правило — строка `Rule(код, решение, условие, пояснение)`. Чтобы
изменить политику, достаточно поправить строку в DEFAULT_RULES или
зарегистрировать новый набор:

    from tifjpg.domain import folder_rules
    folder_rules.register("my_v2", (folder_rules.Rule(...), ...))

Набор выбирается по имени в config.json (`"folder_rules": "default_v1"`).
Порядок важен: срабатывает первое подошедшее правило, последнее должно
подходить всегда.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from tifjpg.domain.models import (
    CONFLICT,
    DONE,
    EMPTY,
    PROCESS_FROM_ARCHIVE,
    PROCESS_ROOT,
    FolderDecision,
)

DEFAULT_NAME = "default_v1"


@dataclass(frozen=True)
class FolderFacts:
    """Числа, на которых строятся правила (всё сравнивается без учёта регистра)."""

    tiffs_in_folder: int
    tiffs_in_archive: int
    jpegs: int
    archive_dir: Optional[str]

    @property
    def total_tiffs(self):
        return self.tiffs_in_folder + self.tiffs_in_archive

    @classmethod
    def of(cls, snapshot, naming):
        return cls(
            tiffs_in_folder=len(snapshot.tiffs()),
            tiffs_in_archive=len(snapshot.archive_tiffs()),
            jpegs=len(snapshot.processed_jpegs(naming)),
            archive_dir=snapshot.archive_dir,
        )


@dataclass(frozen=True)
class Rule:
    code: str
    kind: str
    matches: Callable[[FolderFacts], bool]
    describe: Callable[[FolderFacts], str]


DEFAULT_RULES = (
    Rule(
        "R1", DONE,
        lambda f: f.jpegs > 0 and f.total_tiffs in (f.jpegs, 0),
        lambda f: ("папка уже обработана: JPEG {} шт. = TIFF {} шт.".format(f.jpegs, f.total_tiffs)
                   if f.total_tiffs else "снимков нет, JPEG уже есть ({} шт.)".format(f.jpegs)),
    ),
    Rule(
        "R2", CONFLICT,
        lambda f: f.jpegs > 0,
        lambda f: "JPEG {} шт., а TIFF {} шт. — количество не совпадает".format(f.jpegs, f.total_tiffs),
    ),
    Rule(
        "R5", CONFLICT,
        lambda f: f.tiffs_in_folder > 0 and f.tiffs_in_archive > 0,
        lambda f: "TIFF есть и в папке ({} шт.), и в {} ({} шт.)".format(
            f.tiffs_in_folder, f.archive_dir, f.tiffs_in_archive),
    ),
    Rule(
        "R3", PROCESS_ROOT,
        lambda f: f.tiffs_in_folder > 0,
        lambda f: "TIFF в папке: {} шт.".format(f.tiffs_in_folder),
    ),
    Rule(
        "R4", PROCESS_FROM_ARCHIVE,
        lambda f: f.tiffs_in_archive > 0,
        lambda f: "TIFF только в {}: {} шт., JPEG создаются уровнем выше, рядом с {}".format(
            f.archive_dir, f.tiffs_in_archive, f.archive_dir),
    ),
    Rule(
        "R6", EMPTY,
        lambda f: True,
        lambda f: "ни TIFF, ни JPEG",
    ),
)


def make_classifier(rules):
    # type: (Tuple[Rule, ...]) -> Callable
    def classify(snapshot, naming):
        facts = FolderFacts.of(snapshot, naming)
        for rule in rules:
            if rule.matches(facts):
                return FolderDecision(rule.kind, rule.code, rule.describe(facts))
        raise RuntimeError("набор правил не покрывает все случаи: последнее правило должно подходить всегда")

    return classify


_REGISTRY = {}


def register(name, rules):
    _REGISTRY[name] = make_classifier(rules)


def get(name=DEFAULT_NAME):
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError("неизвестный набор правил {!r}; известные: {}".format(
            name, ", ".join(sorted(_REGISTRY))))


def available():
    return tuple(sorted(_REGISTRY))


register(DEFAULT_NAME, DEFAULT_RULES)

# Текущий набор по умолчанию — для кода, которому не нужен выбор.
classify = get()
