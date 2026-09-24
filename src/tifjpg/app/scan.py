"""Сухой прогон: обход дерева, решения по папкам, планы (ARCHITECTURE.md, 10.1).

Ничего не меняет на диске. Один и тот же результат показывают консоль и
GUI, поэтому и сбор, и отрисовка отчёта живут здесь, в слое приложения.
"""

import os
from dataclasses import dataclass, field
from typing import List, Tuple

from tifjpg.domain import folder_rules
from tifjpg.domain.models import CONFLICT, DONE, EMPTY
from tifjpg.domain.naming import XrayNaming
from tifjpg.fs.scanner import scan_tree
from tifjpg.transaction.planner import plan_folder


@dataclass
class ScanResult:
    root: str
    rules: str
    folders_seen: int = 0
    plans: List = field(default_factory=list)
    errors: List[Tuple[str, Exception]] = field(default_factory=list)

    def by_kind(self, kind):
        return [plan for plan in self.plans if plan.decision.kind == kind]

    @property
    def to_process(self):
        return [plan for plan in self.plans if plan.ok]

    @property
    def blocked(self):
        return [plan for plan in self.plans if plan.decision.processable and plan.problems]

    @property
    def total_files(self):
        return sum(len(plan.files) for plan in self.to_process)


def scan_root(root, naming=None, rules=folder_rules.DEFAULT_NAME, include_empty=False, on_folder=None):
    """Собрать решения и планы по всему дереву. Диск только читается."""
    naming = naming or XrayNaming()
    classify = folder_rules.get(rules)
    result = ScanResult(root=os.path.abspath(root), rules=rules)
    snapshots = scan_tree(root, naming, on_error=lambda path, exc: result.errors.append((path, exc)))
    result.folders_seen = len(snapshots)
    for snapshot in snapshots:
        decision = classify(snapshot, naming)
        if decision.kind == EMPTY and not include_empty:
            continue
        plan = plan_folder(snapshot, decision, naming)
        result.plans.append(plan)
        if on_folder is not None:
            on_folder(plan)
    return result


def render_report(result, full=False, max_files=3):
    """Человекочитаемый отчёт одинакового вида для консоли и GUI."""
    lines = [
        "Корень: {}".format(result.root),
        "Набор правил: {}".format(result.rules),
        "Папок просмотрено: {}".format(result.folders_seen),
        "",
    ]
    _group(lines, "К ОБРАБОТКЕ", result.to_process, result.root, full, max_files, show_files=True)
    _group(lines, "НЕ ОБРАБАТЫВАЛИСЬ (уже готовы)", result.by_kind(DONE), result.root, full, max_files)
    _group(lines, "КОНФЛИКТЫ (папка не тронута)", result.by_kind(CONFLICT), result.root, full, max_files)
    _group(lines, "ОБРАБОТКА НЕВОЗМОЖНА", result.blocked, result.root, full, max_files, show_problems=True)

    if result.errors:
        lines.append("Не удалось прочитать ({}):".format(len(result.errors)))
        lines.extend("  {} — {}".format(path, exc) for path, exc in result.errors)
        lines.append("")

    lines.append("ИТОГО: к обработке {} файлов в {} папках; готовых {}, конфликтов {}, невозможных {}".format(
        result.total_files, len(result.to_process), len(result.by_kind(DONE)),
        len(result.by_kind(CONFLICT)), len(result.blocked)))
    return "\n".join(lines)


def as_dict(result):
    return {
        "root": result.root,
        "rules": result.rules,
        "folders_seen": result.folders_seen,
        "errors": [{"path": path, "error": str(exc)} for path, exc in result.errors],
        "folders": [{
            "path": plan.folder,
            "decision": plan.decision.kind,
            "rule": plan.decision.rule,
            "reason": plan.decision.reason,
            "archive_dir": plan.archive_dir,
            "creates_archive_dir": plan.creates_archive_dir,
            "two_step_renames": plan.two_step_renames,
            "problems": list(plan.problems),
            "files": [{
                "index": planned.index,
                "source": planned.source,
                "jpeg": planned.jpeg,
                "archive": planned.archive,
            } for planned in plan.files],
        } for plan in result.plans],
    }


def _group(lines, title, plans, root, full, max_files, show_files=False, show_problems=False):
    if not plans:
        return
    lines.append("{} ({}):".format(title, len(plans)))
    for plan in plans:
        lines.append("  {}".format(_relative(plan.folder, root)))
        lines.append("      {} — {}".format(plan.decision.rule, plan.decision.reason))
        if show_files:
            shown = plan.files if full else plan.files[:max_files]
            for planned in shown:
                lines.append("      {} -> {}".format(
                    _relative(planned.source, plan.folder), _relative(planned.jpeg, plan.folder)))
                lines.append("         оригинал -> {}".format(_relative(planned.archive, plan.folder)))
            if len(plan.files) > len(shown):
                lines.append("      … ещё {} файл(ов)".format(len(plan.files) - len(shown)))
            if plan.creates_archive_dir:
                lines.append("      будет создана папка {}".format(os.path.basename(plan.archive_dir)))
            if plan.two_step_renames:
                lines.append("      переименование в архиве через временные имена")
        if show_problems:
            lines.extend("      ! {}".format(problem) for problem in plan.problems)
    lines.append("")


def _relative(path, base):
    try:
        relative = os.path.relpath(path, base)
    except ValueError:
        return path
    return "." if relative == "." else relative
