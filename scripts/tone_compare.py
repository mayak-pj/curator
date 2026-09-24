#!/usr/bin/env python3
"""Визуальное сравнение способов перевода 16 -> 8 бит на реальных снимках.

    python scripts/tone_compare.py ts/*.tif* --reference ts/123131.jpg --out out/tone_compare

Создаёт <out>/index.html: превью, фрагменты 1:1, гистограммы с границами
окна и (если задан) сравнение с JPEG прежней программы. Исходные файлы
только читаются.
"""

import argparse
import html
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from tifjpg.imaging.histogram import histogram_stats, percentile_value  # noqa: E402
from tifjpg.imaging.jpeg_info import read_jpeg_header  # noqa: E402
from tifjpg.imaging.tiff_tags import read_tiff_header  # noqa: E402
from tifjpg.imaging.tone import ToneSpec, build_lut, window_from_histogram  # noqa: E402
from tifjpg.imaging.vips_converter import read_histogram  # noqa: E402
from tifjpg.imaging.vips_runtime import load_pyvips, vips_version  # noqa: E402

VARIANTS = (
    ("linear", "Линейный: весь диапазон 0–65535", ToneSpec("linear"), "#6b7280"),
    ("minmax", "Min–max снимка", ToneSpec("minmax"), "#2563eb"),
    ("percentile", "Перцентили 0,5–99,5 %", ToneSpec("percentile", 0.5, 99.5), "#ea580c"),
    ("percentile_inner", "Перцентили 0,5–99,5 % без насыщенных 0 и 65535",
     ToneSpec("percentile", 0.5, 99.5, exclude_extremes=True), "#9333ea"),
)
REFERENCE_COLOR = "#16a34a"
PREVIEW_LONG_SIDE = 1400
CROP = 900
PREVIEW_Q = 92
CROP_Q = 95
DIMENSION_MATCH = 0.015


def lut_image(vips, window):
    return vips.Image.new_from_memory(build_lut(*window), 65536, 1, 1, "uchar")


def mapped(vips, path, window, access):
    image = vips.Image.tiffload(path, access=access)
    return image.maplut(lut_image(vips, window)).copy(interpretation="b-w")


def clamp(value, low, high):
    return max(low, min(value, high))


def detail_origin(vips, preview_path, scale, width, height, crop):
    """Левый верхний угол фрагмента с наибольшей локальной вариативностью (не у края)."""
    preview = vips.Image.new_from_file(preview_path).cast("float")
    block = max(4, int(round(crop * scale)))
    if preview.width < block * 3 or preview.height < block * 3:
        return (width - crop) // 2, (height - crop) // 2
    mean = preview.shrink(block, block)
    variance = (preview * preview).shrink(block, block) - mean * mean
    inner = variance.crop(1, 1, variance.width - 2, variance.height - 2)
    _, where = inner.max(x=True, y=True)
    left = int((where["x"] + 1) * block / scale)
    top = int((where["y"] + 1) * block / scale)
    return clamp(left, 0, width - crop), clamp(top, 0, height - crop)


def clipped_share(counts, window):
    total = sum(counts)
    lo, hi = window
    below = sum(counts[:lo + 1])
    above = sum(counts[hi:])
    return round(100.0 * below / total, 3), round(100.0 * above / total, 3)


def process_tiff(vips, path, index, out_dir):
    started = time.monotonic()
    folder_name = "{:02d}".format(index)
    folder = os.path.join(out_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    header = read_tiff_header(path)
    counts = read_histogram(vips.Image.tiffload(path, access="sequential"))
    stats = histogram_stats(counts)
    width, height = header.width, header.height
    scale = min(1.0, PREVIEW_LONG_SIDE / float(max(width, height)))
    crop = min(CROP, width, height)

    variants = []
    for key, title, spec, color in VARIANTS:
        window = window_from_histogram(counts, spec)
        preview = os.path.join(folder, key + "_preview.jpg")
        mapped(vips, path, window, "sequential").resize(scale, kernel="lanczos3").jpegsave(preview, Q=PREVIEW_Q)
        variants.append({
            "key": key, "title": title, "color": color, "window": list(window),
            "clipped_percent": clipped_share(counts, window),
            "preview": folder_name + "/" + key + "_preview.jpg",
        })

    detail = detail_origin(vips, os.path.join(folder, "percentile_preview.jpg"), scale, width, height, crop)
    center = ((width - crop) // 2, (height - crop) // 2)
    crops = {"detail": detail, "center": center}
    for variant in variants:
        for label, (left, top) in crops.items():
            name = "{}_{}.jpg".format(variant["key"], label)
            image = mapped(vips, path, variant["window"], "random").crop(left, top, crop, crop)
            image.jpegsave(os.path.join(folder, name), Q=CROP_Q)
            variant[label] = folder_name + "/" + name

    return {
        "path": path, "name": os.path.basename(path), "folder": folder_name,
        "file_size": os.path.getsize(path), "header": header.summary(), "stats": stats,
        "crop": crop, "crops": {k: list(v) for k, v in crops.items()},
        "variants": variants, "counts": counts, "seconds": round(time.monotonic() - started, 1),
    }


def process_reference(vips, path, out_dir):
    folder = os.path.join(out_dir, "reference")
    os.makedirs(folder, exist_ok=True)
    header = read_jpeg_header(path)
    counts = list(read_histogram(vips.Image.jpegload(path, access="sequential")))
    scale = min(1.0, PREVIEW_LONG_SIDE / float(max(header.width, header.height)))
    vips.Image.jpegload(path, access="sequential").resize(scale, kernel="lanczos3").jpegsave(
        os.path.join(folder, "preview.jpg"), Q=PREVIEW_Q)
    crop = min(CROP, header.width, header.height)
    left, top = (header.width - crop) // 2, (header.height - crop) // 2
    vips.Image.jpegload(path).crop(left, top, crop, crop).jpegsave(os.path.join(folder, "center.jpg"), Q=CROP_Q)
    return {
        "path": path, "name": os.path.basename(path), "header": header.summary(),
        "stats": histogram_stats(counts), "counts": counts,
        "preview": "reference/preview.jpg", "center": "reference/center.jpg",
    }


def dimensions_match(tiff_report, reference):
    tw, th = tiff_report["header"]["width"], tiff_report["header"]["height"]
    rw, rh = reference["header"]["width"], reference["header"]["height"]
    return abs(tw - rw) <= tw * DIMENSION_MATCH and abs(th - rh) <= th * DIMENSION_MATCH


def fit_reference(counts16, counts8):
    """Подбор линейной зависимости v8 = a*v16 + b по парам перцентилей."""
    points = []
    for percent in range(1, 100):
        v16 = percentile_value(counts16, percent)
        v8 = percentile_value(counts8, percent)
        points.append((v16, v8))
    usable = [(x, y) for x, y in points if 2 <= y <= 253]
    if len(usable) < 5:
        return {"points": points, "fit": None}
    n = float(len(usable))
    mean_x = sum(x for x, _ in usable) / n
    mean_y = sum(y for _, y in usable) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in usable)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in usable)
    if not sxx:
        return {"points": points, "fit": None}
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    rms = math.sqrt(sum((y - (slope * x + intercept)) ** 2 for x, y in usable) / n)
    window = None
    if slope:
        window = [int(round(-intercept / slope)), int(round((255 - intercept) / slope))]
    return {"points": points, "fit": {"slope": slope, "intercept": intercept, "rms": round(rms, 2),
                                      "window": window, "used_points": len(usable)}}


# ---------------------------------------------------------------- HTML

def histogram_svg(counts, x_min, x_max, markers, title, width=620, height=170, bins=310):
    pad_left, pad_bottom, pad_top = 8, 22, 18
    plot_w, plot_h = width - 2 * pad_left, height - pad_bottom - pad_top
    span = x_max - x_min + 1
    bins = min(bins, span)
    heights = []
    for b in range(bins):
        lo = x_min + int(b * span / bins)
        hi = max(lo + 1, x_min + int((b + 1) * span / bins))
        heights.append(sum(counts[lo:hi]))
    peak = math.log1p(max(heights) or 1)
    bar_w = plot_w / float(bins)
    parts = ['<svg viewBox="0 0 {w} {h}" class="hist" role="img">'.format(w=width, h=height),
             '<text x="{}" y="12" class="t">{}</text>'.format(pad_left, html.escape(title))]
    for b, value in enumerate(heights):
        if not value:
            continue
        bar_h = plot_h * math.log1p(value) / peak
        parts.append('<rect x="{:.2f}" y="{:.2f}" width="{:.2f}" height="{:.2f}" class="bar"/>'.format(
            pad_left + b * bar_w, pad_top + plot_h - bar_h, max(bar_w, 0.6), bar_h))
    for value, color, dash in markers:
        if x_min <= value <= x_max:
            x = pad_left + (value - x_min) / float(span) * plot_w
            parts.append('<line x1="{x:.1f}" x2="{x:.1f}" y1="{t}" y2="{b}" stroke="{c}" stroke-width="2" '
                         'stroke-dasharray="{d}"/>'.format(x=x, t=pad_top, b=pad_top + plot_h, c=color, d=dash))
    parts.append('<text x="{}" y="{}" class="a">{}</text>'.format(pad_left, height - 6, x_min))
    parts.append('<text x="{}" y="{}" class="a" text-anchor="end">{}</text>'.format(width - pad_left, height - 6, x_max))
    parts.append("</svg>")
    return "".join(parts)


def mapping_svg(points, lines, x_min, x_max, width=620, height=260):
    pad = 34
    plot_w, plot_h = width - 2 * pad, height - 2 * pad

    def sx(v):
        return pad + (v - x_min) / float(x_max - x_min) * plot_w

    def sy(v):
        return pad + plot_h - v / 255.0 * plot_h

    parts = ['<svg viewBox="0 0 {} {}" class="hist" role="img">'.format(width, height),
             '<rect x="{}" y="{}" width="{}" height="{}" class="frame"/>'.format(pad, pad, plot_w, plot_h),
             '<text x="{}" y="16" class="t">16-бит значение (ось X) → 8-бит значение (ось Y), по перцентилям 1–99 %</text>'.format(pad)]
    for (lo, hi), color, dash in lines:
        # Кривая окна: 0 до lo, линейный рост до hi, 255 после — в пределах оси X.
        xs = sorted({x_min, clamp(lo, x_min, x_max), clamp(hi, x_min, x_max), x_max})
        coords = " ".join("{:.1f},{:.1f}".format(sx(x), sy(clamp((x - lo) * 255.0 / (hi - lo), 0, 255))) for x in xs)
        parts.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2" stroke-dasharray="{}"/>'.format(
            coords, color, dash))
    for x, y in points:
        if x_min <= x <= x_max:
            parts.append('<circle cx="{:.1f}" cy="{:.1f}" r="2.6" fill="{}"/>'.format(sx(x), sy(y), REFERENCE_COLOR))
    parts.append('<text x="{}" y="{}" class="a">{}</text>'.format(pad, height - 10, x_min))
    parts.append('<text x="{}" y="{}" class="a" text-anchor="end">{}</text>'.format(width - pad, height - 10, x_max))
    parts.append("</svg>")
    return "".join(parts)


CSS = """
:root { color-scheme: light; }
body { font: 14px/1.45 -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 24px 32px 64px; background: #f7f7f5; color: #1f2328; }
h1 { font-size: 22px; margin: 0 0 6px; }
h2 { font-size: 18px; margin: 40px 0 8px; padding-top: 18px; border-top: 1px solid #d8dadd; }
h3 { font-size: 15px; margin: 22px 0 8px; }
.note { color: #57606a; max-width: 980px; }
nav a { margin-right: 14px; }
table.facts { border-collapse: collapse; margin: 8px 0 14px; font-variant-numeric: tabular-nums; }
table.facts td, table.facts th { padding: 3px 12px 3px 0; text-align: left; vertical-align: top; }
table.facts th { color: #57606a; font-weight: 500; }
.hists { display: flex; flex-wrap: wrap; gap: 12px; }
svg.hist { width: 620px; max-width: 100%; background: #fff; border: 1px solid #e1e4e8; border-radius: 6px; }
svg .bar { fill: #9aa4ae; } svg .t { font-size: 12px; fill: #1f2328; } svg .a { font-size: 11px; fill: #57606a; }
svg .frame { fill: none; stroke: #e1e4e8; }
.legend span { display: inline-block; margin-right: 16px; }
.legend i { display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 6px; }
.grid { display: grid; gap: 10px; align-items: start; }
.cell { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px; padding: 8px; }
.cell img { width: 100%; display: block; background: #000; }
.cell .h { font-weight: 600; border-left: 4px solid; padding-left: 8px; margin-bottom: 4px; }
.cell .s { color: #57606a; font-size: 12px; margin-bottom: 6px; font-variant-numeric: tabular-nums; }
.rowlabel { font-weight: 600; margin: 14px 0 4px; }
.fit { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px; padding: 10px 14px; max-width: 980px; }
"""


def render(reports, reference, out_dir):
    out = ['<!doctype html><html lang="ru"><head><meta charset="utf-8">',
           "<title>Сравнение тон-маппинга 16→8 бит</title><style>{}</style></head><body>".format(CSS),
           "<h1>Перевод 16 → 8 бит: сравнение вариантов</h1>",
           '<p class="note">libvips {}. Превью уменьшены до {} px по длинной стороне. <b>Фрагменты {}×{} — в масштабе 1:1</b>, '
           "по ним видно реальное качество (контраст в тенях и светах, шум). «Детали» — участок с наибольшей локальной вариативностью. "
           "Щелчок по картинке открывает её отдельно. «Обрезано» — доля пикселей, ставших чисто чёрными/белыми.</p>".format(
               html.escape(vips_version()), PREVIEW_LONG_SIDE, CROP, CROP)]
    out.append("<nav>" + "".join('<a href="#f{0}">{1}</a>'.format(r["folder"], html.escape(r["name"])) for r in reports) + "</nav>")

    for report in reports:
        header, stats = report["header"], report["stats"]
        pct = stats["percentiles"]
        matched = reference is not None and dimensions_match(report, reference)
        out.append('<h2 id="f{}">{}</h2>'.format(report["folder"], html.escape(report["name"])))
        facts = [
            ("Размер", "{:.1f} МБ, {}×{} ({:.0f} Мпикс)".format(report["file_size"] / 1e6, header["width"], header["height"],
                                                             header["width"] * header["height"] / 1e6)),
            ("Формат", "{} бит, {} канал, {}, сжатие: {}, {} {}".format(
                header["bits_per_sample"], header["samples_per_pixel"], header["photometric"], header["compression"],
                header["chunk_count"], header["layout"])),
            ("Значения", "min {} · p0,5 {} · p1 {} · p50 {} · p99 {} · p99,5 {} · max {}".format(
                stats["min"], pct["0.5"], pct["1.0"], pct["50.0"], pct["99.0"], pct["99.5"], stats["max"])),
            ("Используется", "{} бит из 16 (max {}), различных значений: {}".format(
                stats["effective_bits"], stats["max"], stats["distinct_values"])),
            ("Насыщено", "ровно 0: {:.2f} % пикселей · ровно 65535: {:.2f} %".format(
                100.0 * report["counts"][0] / stats["pixels"], 100.0 * report["counts"][65535] / stats["pixels"])),
        ]
        if header.get("software") or header.get("make"):
            facts.append(("ПО / аппарат", html.escape(" · ".join(str(v) for v in (header.get("software"), header.get("make"), header.get("model")) if v))))
        out.append('<table class="facts">' + "".join("<tr><th>{}</th><td>{}</td></tr>".format(k, v) for k, v in facts) + "</table>")

        markers = []
        for variant in report["variants"]:
            dash = "6 3" if variant["key"] == "linear" else "none"
            markers.append((variant["window"][0], variant["color"], dash))
            markers.append((variant["window"][1], variant["color"], dash))
        zoom_lo = max(0, stats["min"] - (stats["max"] - stats["min"]) // 20)
        zoom_hi = min(65535, stats["max"] + (stats["max"] - stats["min"]) // 20)
        out.append('<div class="hists">')
        out.append(histogram_svg(report["counts"], 0, 65535, markers, "Гистограмма, весь диапазон 0–65535 (лог. шкала)"))
        out.append(histogram_svg(report["counts"], zoom_lo, max(zoom_hi, zoom_lo + 1), markers, "Гистограмма, приближено к данным"))
        out.append("</div>")
        out.append('<p class="legend">' + "".join(
            '<span><i style="background:{}"></i>{}</span>'.format(v["color"], html.escape(v["title"])) for v in report["variants"]) + "</p>")

        columns = len(report["variants"]) + (1 if matched else 0)
        grid_style = 'style="grid-template-columns: repeat({}, minmax(0, 1fr))"'.format(columns)

        def header_cells():
            cells = []
            for v in report["variants"]:
                cells.append('<div class="cell"><div class="h" style="border-color:{}">{}</div>'
                             '<div class="s">окно [{} … {}] · обрезано: {} % в чёрное, {} % в белое</div></div>'.format(
                                 v["color"], html.escape(v["title"]), v["window"][0], v["window"][1],
                                 v["clipped_percent"][0], v["clipped_percent"][1]))
            if matched:
                cells.append('<div class="cell"><div class="h" style="border-color:{}">Прежняя программа: {}</div>'
                             '<div class="s">{}×{}, Q≈{}, {:.1f} МБ — кадр не совпадает попиксельно</div></div>'.format(
                                 REFERENCE_COLOR, html.escape(reference["name"]), reference["header"]["width"],
                                 reference["header"]["height"], reference["header"]["estimated_quality"],
                                 reference["header"]["file_size"] / 1e6))
            return cells

        def image_row(label, key, reference_key):
            cells = ['<div class="cell"><a href="{0}"><img loading="lazy" src="{0}"></a></div>'.format(v[key]) for v in report["variants"]]
            if matched:
                if reference_key:
                    cells.append('<div class="cell"><a href="{0}"><img loading="lazy" src="{0}"></a></div>'.format(reference[reference_key]))
                else:
                    cells.append('<div class="cell s">— нет совпадающего фрагмента</div>')
            return '<div class="rowlabel">{}</div><div class="grid" {}>{}</div>'.format(label, grid_style, "".join(cells))

        out.append('<div class="grid" {}>{}</div>'.format(grid_style, "".join(header_cells())))
        out.append(image_row("Превью целиком", "preview", "preview"))
        out.append(image_row("Детали 1:1 (x={}, y={})".format(*report["crops"]["detail"]), "detail", None))
        out.append(image_row("Центр 1:1 (x={}, y={})".format(*report["crops"]["center"]), "center", "center"))

        if matched:
            fitted = fit_reference(report["counts"], reference["counts"])
            report["reference_fit"] = fitted["fit"]
            lines = [(tuple(v["window"]), v["color"], "6 3" if v["key"] == "linear" else "none") for v in report["variants"]]
            out.append("<h3>Как переводила прежняя программа (оценка по гистограммам)</h3><div class=\"fit\">")
            if fitted["fit"] and fitted["fit"]["window"]:
                fit = fitted["fit"]
                lo, hi = fit["window"]
                if hi > lo:
                    lines.append(((lo, hi), REFERENCE_COLOR, "2 3"))
                out.append("<p>Точки — соответствие значений по перцентилям 1–99 % между этим TIFF и {}. "
                           "Линейная аппроксимация: окно ≈ [{} … {}], отклонение {} уровней из 255 "
                           "(близко к 0 — перевод линейный; большое — кривая/гамма или это другой снимок).</p>".format(
                               html.escape(reference["name"]), lo, hi, fit["rms"]))
            else:
                out.append("<p>Недостаточно данных для аппроксимации.</p>")
            x_lo = min(p[0] for p in fitted["points"])
            x_hi = max(p[0] for p in fitted["points"])
            margin = max(1, (x_hi - x_lo) // 10)
            out.append(mapping_svg(fitted["points"], lines, max(0, x_lo - margin), min(65535, x_hi + margin)))
            out.append("</div>")

    if reference is not None:
        out.append("<h2>Прежняя программа: {}</h2>".format(html.escape(reference["name"])))
        rh = reference["header"]
        rp = reference["stats"]["percentiles"]
        out.append('<table class="facts"><tr><th>JPEG</th><td>{}×{}, {} компонент, {} бит, Q≈{}{}, {}, {:.1f} МБ</td></tr>'
                   "<tr><th>Значения</th><td>min {} · p0,5 {} · p50 {} · p99,5 {} · max {}</td></tr></table>".format(
                       rh["width"], rh["height"], rh["components"], rh["precision"], rh["estimated_quality"],
                       "" if rh["quality_exact"] else " (нестандартные таблицы)",
                       "progressive" if rh["progressive"] else "baseline", rh["file_size"] / 1e6,
                       reference["stats"]["min"], rp["0.5"], rp["50.0"], rp["99.5"], reference["stats"]["max"]))
        out.append(histogram_svg(reference["counts"], 0, 255, [], "Гистограмма 8-бит (лог. шкала)"))

    out.append("</body></html>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as stream:
        stream.write("\n".join(out))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tiffs", nargs="+")
    parser.add_argument("--reference", help="JPEG прежней программы для сравнения")
    parser.add_argument("--out", default="out/tone_compare")
    args = parser.parse_args(argv)

    vips = load_pyvips()
    os.makedirs(args.out, exist_ok=True)
    reports = []
    for index, path in enumerate(args.tiffs, start=1):
        print("[{}/{}] {}".format(index, len(args.tiffs), path), flush=True)
        report = process_tiff(vips, path, index, args.out)
        print("    готово за {} с; окна: {}".format(
            report["seconds"], ", ".join("{} {}".format(v["key"], v["window"]) for v in report["variants"])), flush=True)
        reports.append(report)
    reference = process_reference(vips, args.reference, args.out) if args.reference else None

    render(reports, reference, args.out)
    summary = [{k: v for k, v in r.items() if k != "counts"} for r in reports]
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=1)
    print("HTML: {}".format(os.path.abspath(os.path.join(args.out, "index.html"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
