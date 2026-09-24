#!/usr/bin/env python3
"""Отчёт по TIFF/JPEG: теги, реальные диапазоны значений, гистограммы.

    python scripts/tiff_inspect.py ts/ --json out/inspect/report.json

Файлы только читаются.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from tifjpg.imaging.histogram import downsample, histogram_stats  # noqa: E402
from tifjpg.imaging.jpeg_info import read_jpeg_header  # noqa: E402
from tifjpg.imaging.tiff_tags import read_tiff_header  # noqa: E402
from tifjpg.imaging.tone import ToneSpec, window_from_histogram  # noqa: E402
from tifjpg.imaging.vips_converter import read_histogram  # noqa: E402
from tifjpg.imaging.vips_runtime import load_pyvips, vips_version  # noqa: E402

TIFF_EXTENSIONS = (".tif", ".tiff")
JPEG_EXTENSIONS = (".jpg", ".jpeg")
HISTOGRAM_BINS = 512
TONE_SPECS = (ToneSpec("linear"), ToneSpec("minmax"), ToneSpec("percentile", 0.5, 99.5))


def iter_files(paths):
    for path in paths:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs.sort()
                for name in sorted(files):
                    yield os.path.join(root, name)
        else:
            yield path


def inspect_tiff(vips, path):
    header = read_tiff_header(path)
    image = vips.Image.tiffload(path, access="sequential")
    started = time.monotonic()
    counts = read_histogram(image)
    seconds = time.monotonic() - started
    return {
        "path": path,
        "kind": "tiff",
        "file_size": os.path.getsize(path),
        "header": header.summary(),
        "vips": {
            "format": image.format,
            "bands": image.bands,
            "interpretation": image.interpretation,
            "xres_px_per_mm": image.xres,
            "yres_px_per_mm": image.yres,
        },
        "stats": histogram_stats(counts),
        "windows": {spec.describe(): list(window_from_histogram(counts, spec)) for spec in TONE_SPECS},
        "histogram_seconds": round(seconds, 2),
        "histogram_bin_width": max(1, len(counts) // HISTOGRAM_BINS),
        "histogram": downsample(counts, HISTOGRAM_BINS),
    }


def inspect_jpeg(vips, path):
    header = read_jpeg_header(path)
    image = vips.Image.jpegload(path, access="sequential")
    counts = read_histogram(image)
    return {
        "path": path,
        "kind": "jpeg",
        "file_size": header.file_size,
        "header": header.summary(),
        "stats": histogram_stats(counts),
        "histogram_bin_width": 1,
        "histogram": list(counts),
    }


def print_table(reports):
    columns = ("файл", "МБ", "размер", "бит", "сжатие", "photometric", "min", "p0.5", "p50", "p99.5", "max", "eff.bits")
    rows = []
    for report in reports:
        header = report["header"]
        stats = report["stats"]
        percentiles = stats["percentiles"]
        if report["kind"] == "tiff":
            bits = header["bits_per_sample"]
            compression = header["compression"]
            photometric = header["photometric"]
        else:
            bits = header["precision"]
            compression = "jpeg Q≈{}".format(header["estimated_quality"])
            photometric = "{} comp".format(header["components"])
        rows.append((
            os.path.basename(report["path"]),
            "{:.1f}".format(report["file_size"] / 1e6),
            "{}x{}".format(header["width"], header["height"]),
            str(bits),
            compression,
            str(photometric),
            str(stats["min"]),
            str(percentiles["0.5"]),
            str(percentiles["50.0"]),
            str(percentiles["99.5"]),
            str(stats["max"]),
            str(stats["effective_bits"]),
        ))
    widths = [max(len(columns[i]), *(len(row[i]) for row in rows)) for i in range(len(columns))]
    line = "  ".join(name.ljust(width) for name, width in zip(columns, widths))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))

    print()
    for report in reports:
        if report["kind"] != "tiff":
            continue
        windows = ", ".join("{} [{}, {}]".format(name, lo, hi) for name, (lo, hi) in report["windows"].items())
        extra = report["header"]
        print("{}: окна: {}".format(os.path.basename(report["path"]), windows))
        print("    layout={} chunks={} rows/strip={} pages={} res={} software={!r} гистограмма {:.1f} с".format(
            extra["layout"], extra["chunk_count"], extra["rows_per_strip"], extra["page_count"],
            extra["resolution"], extra["software"], report["histogram_seconds"],
        ))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--json", help="сохранить полный отчёт в JSON")
    args = parser.parse_args(argv)

    vips = load_pyvips()
    print("libvips {}".format(vips_version()))
    reports = []
    for path in iter_files(args.paths):
        extension = os.path.splitext(path)[1].lower()
        if extension in TIFF_EXTENSIONS:
            reports.append(inspect_tiff(vips, path))
        elif extension in JPEG_EXTENSIONS:
            reports.append(inspect_jpeg(vips, path))
    if not reports:
        print("TIFF/JPEG не найдены")
        return 1

    print_table(reports)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as stream:
            json.dump(reports, stream, ensure_ascii=False, indent=1)
        print("\nJSON: {}".format(args.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
