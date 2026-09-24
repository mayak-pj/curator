"""Консольный интерфейс для проверки ядра без GUI.

    python -m tifjpg scan ROOT                       # сухой прогон: что было бы сделано
    python -m tifjpg convert SRC.tif DST.jpeg        # linear, Q100 (решение D12)
    python -m tifjpg convert SRC.tif DST.jpeg --tone percentile --p-low 0.5 --p-high 99.5

Команда scan ничего не меняет на диске.
"""

import argparse
import json
import os
import sys

from tifjpg.domain.errors import TifJpgError


def build_parser():
    parser = argparse.ArgumentParser(prog="tifjpg")
    commands = parser.add_subparsers(dest="command")
    commands.required = True

    convert = commands.add_parser("convert", help="конвертировать один файл")
    convert.add_argument("source")
    convert.add_argument("destination")
    convert.add_argument("--tone", default="linear", choices=("linear", "minmax", "percentile"))
    convert.add_argument("--p-low", type=float, default=0.5)
    convert.add_argument("--p-high", type=float, default=99.5)
    convert.add_argument("--exclude-extremes", action="store_true",
                         help="не учитывать пиксели 0 и 65535 при расчёте окна")
    convert.add_argument("--quality", type=int, default=100)
    convert.add_argument("--no-validate", action="store_true", help="не проверять готовый JPEG")
    convert.set_defaults(handler=cmd_convert)

    scan = commands.add_parser("scan", help="сухой прогон по дереву папок, без изменений на диске")
    scan.add_argument("root")
    scan.add_argument("--full", action="store_true", help="показывать все файлы, а не первые три")
    scan.add_argument("--empty", action="store_true", help="показывать и папки без снимков")
    scan.add_argument("--json", help="сохранить отчёт в JSON")
    scan.set_defaults(handler=cmd_scan)
    return parser


def cmd_convert(args):
    from tifjpg.imaging import ConversionOptions, ToneSpec, converter_for, validate_jpeg

    options = ConversionOptions(
        tone=ToneSpec(args.tone, p_low=args.p_low, p_high=args.p_high, exclude_extremes=args.exclude_extremes),
        quality=args.quality,
    )
    converter = converter_for(args.source)
    result = converter.convert(args.source, args.destination, options, progress=_print_progress)
    sys.stderr.write("\n")
    report = result.as_dict()
    if not args.no_validate:
        check = validate_jpeg(result.destination, result.width, result.height, result.bands)
        report["validated"] = True
        report["estimated_quality"] = check.estimated_quality
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args):
    from tifjpg.domain import folder_rules
    from tifjpg.domain.models import CONFLICT, DONE, EMPTY
    from tifjpg.domain.naming import XrayNaming
    from tifjpg.fs.scanner import scan_tree
    from tifjpg.transaction.planner import plan_folder

    naming = XrayNaming()
    errors = []
    snapshots = scan_tree(args.root, naming, on_error=lambda path, exc: errors.append((path, exc)))

    plans = []
    for snapshot in snapshots:
        decision = folder_rules.classify(snapshot, naming)
        if decision.kind == EMPTY and not args.empty:
            continue
        plans.append(plan_folder(snapshot, decision, naming))

    to_process = [plan for plan in plans if plan.ok]
    blocked = [plan for plan in plans if plan.decision.processable and plan.problems]
    done = [plan for plan in plans if plan.decision.kind == DONE]
    conflicts = [plan for plan in plans if plan.decision.kind == CONFLICT]

    print("Корень: {}".format(os.path.abspath(args.root)))
    print("Папок просмотрено: {}".format(len(snapshots)))
    print()
    _print_group("К ОБРАБОТКЕ", to_process, args, show_files=True)
    _print_group("НЕ ОБРАБАТЫВАЛИСЬ (уже готовы)", done, args)
    _print_group("КОНФЛИКТЫ (папка не тронута)", conflicts, args)
    _print_group("ОБРАБОТКА НЕВОЗМОЖНА", blocked, args, show_problems=True)

    if errors:
        print("Не удалось прочитать ({}):".format(len(errors)))
        for path, exc in errors:
            print("  {} — {}".format(path, exc))
        print()

    total_files = sum(len(plan.files) for plan in to_process)
    print("ИТОГО: к обработке {} файлов в {} папках; готовых {}, конфликтов {}, невозможных {}".format(
        total_files, len(to_process), len(done), len(conflicts), len(blocked)))

    if args.json:
        _write_json(args, snapshots, plans, errors)
        print("JSON: {}".format(args.json))
    return 1 if (conflicts or blocked or errors) else 0


def _print_group(title, plans, args, show_files=False, show_problems=False):
    if not plans:
        return
    print("{} ({}):".format(title, len(plans)))
    for plan in plans:
        print("  {}".format(_relative(plan.folder, args.root)))
        print("      {} — {}".format(plan.decision.rule, plan.decision.reason))
        if show_files:
            shown = plan.files if args.full else plan.files[:3]
            for planned in shown:
                print("      {} -> {} + {}{}{}".format(
                    os.path.basename(planned.source),
                    os.path.basename(planned.jpeg),
                    os.path.basename(plan.archive_dir), os.sep,
                    os.path.basename(planned.archive)))
            if len(plan.files) > len(shown):
                print("      … ещё {} файл(ов)".format(len(plan.files) - len(shown)))
            if plan.creates_archive_dir:
                print("      будет создана папка {}".format(os.path.basename(plan.archive_dir)))
            if plan.two_step_renames:
                print("      переименование в архиве через временные имена")
        if show_problems:
            for problem in plan.problems:
                print("      ! {}".format(problem))
    print()


def _relative(path, root):
    try:
        relative = os.path.relpath(path, root)
    except ValueError:
        return path
    return "." if relative == "." else relative


def _write_json(args, snapshots, plans, errors):
    report = {
        "root": os.path.abspath(args.root),
        "folders_seen": len(snapshots),
        "errors": [{"path": path, "error": str(exc)} for path, exc in errors],
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
        } for plan in plans],
    }
    directory = os.path.dirname(os.path.abspath(args.json))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)


def _print_progress(stage, fraction):
    sys.stderr.write("\r{:<10} {:5.1f}%".format(stage, fraction * 100))
    sys.stderr.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (TifJpgError, ValueError, OSError) as exc:
        sys.stderr.write("\nОшибка: {}\n".format(exc))
        return 2
