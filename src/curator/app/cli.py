"""Консольный интерфейс для проверки ядра без GUI.

    python -m curator scan ROOT                       # сухой прогон: что было бы сделано
    python -m curator convert SRC.tif DST.jpeg        # linear, Q100 (решение D12)
    python -m curator convert SRC.tif DST.jpeg --tone percentile --p-low 0.5 --p-high 99.5

Команда scan ничего не меняет на диске.
"""

import argparse
import json
import os
import sys

from curator.domain import folder_rules
from curator.domain.errors import CuratorError


def build_parser():
    parser = argparse.ArgumentParser(prog="curator")
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
    scan.add_argument("--rules", default=folder_rules.DEFAULT_NAME,
                      choices=folder_rules.available(), help="набор правил классификации папок")
    scan.add_argument("--json", help="сохранить отчёт в JSON")
    scan.set_defaults(handler=cmd_scan)

    process = commands.add_parser("process", help="обработать дерево папок (изменяет файлы)")
    process.add_argument("root")
    process.add_argument("--rollback-all", action="store_true",
                         help="сразу откатить всё, что было обработано (проверка отката)")
    process.set_defaults(handler=cmd_process)

    recover = commands.add_parser("recover", help="незавершённые операции прошлых запусков")
    recover.add_argument("--action", choices=("show", "continue", "rollback"), default="show")
    recover.set_defaults(handler=cmd_recover)
    return parser


def cmd_convert(args):
    from curator.imaging import ConversionOptions, ToneSpec, converter_for, validate_jpeg

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
    from curator.app.scan import as_dict, render_report, scan_root
    from curator.domain.models import CONFLICT

    result = scan_root(args.root, rules=args.rules, include_empty=args.empty)
    print(render_report(result, full=args.full))

    if args.json:
        directory = os.path.dirname(os.path.abspath(args.json))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump(as_dict(result), stream, ensure_ascii=False, indent=1)
        print("JSON: {}".format(args.json))

    return 1 if (result.by_kind(CONFLICT) or result.blocked or result.errors) else 0


def cmd_process(args):
    from curator.app import events as ev
    from curator.app.service import ProcessingService

    def show(event):
        kind = event.get("kind")
        if kind == ev.FOLDER_STARTED:
            print("папка {} ({} файлов)".format(event["folder"], event["files"]))
        elif kind == ev.FILE_COMPLETED:
            print("   готов файл #{}".format(event["index"]))
        elif kind in (ev.PROBLEM, ev.FOLDER_SKIPPED):
            print("   ! {}".format(event.get("message") or event.get("reason")))
        elif kind == ev.RETRY:
            print("   повтор {}: попытка {}, пауза {} с".format(
                event["operation"], event["attempt"], event["delay"]))

    service = ProcessingService(on_event=show)
    summary = service.start(args.root)
    print("\nУспешно {}, с ошибками {}, пропущено занятых {}, файлов обработано {}, за {} с".format(
        len(summary.succeeded), len(summary.failed), summary.skipped_locked, summary.files, summary.seconds))
    for outcome in summary.failed:
        print("  {} — {}{}".format(outcome.folder, outcome.error,
                                   " (откат выполнен)" if outcome.rolled_back else ""))
    if args.rollback_all:
        for result in service.rollback_all():
            print("откат {}: {} {}".format(result.folder, result.state, "; ".join(result.warnings)))
        service.close()
    else:
        service.accept()
    print("Журнал и логи: {}".format(service.paths.base))
    return 0 if not summary.failed else 1


def cmd_recover(args):
    from curator.app.service import CONTINUE, ROLLBACK, ProcessingService

    service = ProcessingService()
    found = service.find_recovery()
    if not found:
        print("Незавершённых операций не найдено")
        return 0
    for journal_path, item in found:
        print("{}\n   состояние: {}\n   журнал: {}".format(item.folder, item.situation, journal_path))
        for detail in item.details:
            print("   {}".format(detail))
        if args.action != "show":
            result = service.recover(item, CONTINUE if args.action == "continue" else ROLLBACK)
            print("   результат: {}".format(getattr(result, "state", "готово")))
    service.close()
    return 0


def _print_progress(stage, fraction):
    sys.stderr.write("\r{:<10} {:5.1f}%".format(stage, fraction * 100))
    sys.stderr.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (CuratorError, ValueError, OSError) as exc:
        sys.stderr.write("\nОшибка: {}\n".format(exc))
        return 2
