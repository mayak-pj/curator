"""Консольный интерфейс для проверки ядра без GUI.

    python -m tifjpg convert SRC.tif DST.jpeg                  # linear, Q100 (решение D12)
    python -m tifjpg convert SRC.tif DST.jpeg --tone percentile --p-low 0.5 --p-high 99.5
"""

import argparse
import json
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
