"""Планирование обработки папки (ARCHITECTURE.md, раздел 10.1).

План строится до единого изменения на диске и содержит все будущие имена.
Здесь же ловятся условия, при которых папку трогать нельзя: занятые имена
и слишком длинные пути Windows.
"""

import os

from tifjpg.domain.models import PROCESS_FROM_ARCHIVE, FolderPlan, PlannedFile
from tifjpg.domain.naming import normalize
from tifjpg.domain.ordering import natural_sorted

# Windows 7: длинные пути для сторонних DLL ненадёжны, поэтому не превышаем MAX_PATH.
MAX_PATH = 259
PART_SUFFIX = ".part"


def plan_folder(snapshot, decision, naming, max_path=MAX_PATH):
    """FolderSnapshot + FolderDecision -> FolderPlan (ничего не меняя на диске)."""
    if not decision.processable:
        return FolderPlan(folder=snapshot.path, decision=decision)

    from_archive = decision.kind == PROCESS_FROM_ARCHIVE
    archive_path = snapshot.archive_path(naming)
    sources = natural_sorted(snapshot.archive_tiffs() if from_archive else snapshot.tiffs())

    files = []
    for index, name in enumerate(sources, start=1):
        source_dir = archive_path if from_archive else snapshot.path
        files.append(PlannedFile(
            index=index,
            source=os.path.join(source_dir, name),
            jpeg=os.path.join(snapshot.path, naming.jpeg_name(index)),
            archive=os.path.join(archive_path, naming.source_name(index)),
            source_in_archive=from_archive,
        ))

    problems = _check(snapshot, files, naming, from_archive, max_path)
    return FolderPlan(
        folder=snapshot.path,
        decision=decision,
        files=tuple(files),
        archive_dir=archive_path,
        creates_archive_dir=snapshot.archive_dir is None,
        problems=tuple(problems),
        two_step_renames=_needs_two_step(files, from_archive),
    )


def _needs_two_step(files, from_archive):
    """Целевое имя занято другим исходником: переименование через временные имена."""
    if not from_archive:
        return False
    sources = {normalize(os.path.basename(planned.source)) for planned in files}
    for planned in files:
        target = normalize(os.path.basename(planned.archive))
        if target in sources and not planned.archive_unchanged:
            return True
    return False


def _check(snapshot, files, naming, from_archive, max_path):
    problems = []
    existing_root = {normalize(name) for name in snapshot.files}
    existing_archive = {normalize(name) for name in snapshot.archive_files}
    # Исходники, которые сами будут переименованы, не считаются помехой.
    sources = {normalize(os.path.basename(planned.source)) for planned in files}

    for planned in files:
        jpeg_name = normalize(os.path.basename(planned.jpeg))
        if jpeg_name in existing_root:
            problems.append("файл {} уже существует".format(os.path.basename(planned.jpeg)))
        if normalize(os.path.basename(planned.jpeg) + PART_SUFFIX) in existing_root:
            problems.append("остался временный файл {}{}".format(os.path.basename(planned.jpeg), PART_SUFFIX))

        archive_name = normalize(os.path.basename(planned.archive))
        # При обработке из архива целевое имя может быть занято другим исходником
        # той же папки — это не помеха, переименование делается в два шага.
        if archive_name in existing_archive and not (from_archive and archive_name in sources):
            problems.append("в архиве уже есть {}".format(os.path.basename(planned.archive)))

        for path in (planned.jpeg, planned.archive, planned.jpeg + PART_SUFFIX, planned.archive + PART_SUFFIX):
            if len(path) > max_path:
                problems.append("путь длиннее {} символов: {}".format(max_path, path))
                break

    # Одно и то же имя не должно встретиться дважды (защита от ошибки схемы именования).
    for attribute in ("jpeg", "archive"):
        names = [normalize(getattr(planned, attribute)) for planned in files]
        if len(set(names)) != len(names):
            problems.append("схема именования даёт повторяющиеся имена ({})".format(attribute))

    return problems
