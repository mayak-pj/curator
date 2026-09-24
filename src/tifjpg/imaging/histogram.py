"""Статистика по гистограмме (чистый Python): для отчётов и проверок."""

import math
from typing import Dict, List, Sequence

REPORT_PERCENTILES = (0.01, 0.1, 0.5, 1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0, 99.5, 99.9, 99.99)


def percentile_value(counts, percent):
    # type: (Sequence[int], float) -> int
    """Наименьшее значение v, для которого доля пикселей <= v не меньше percent."""
    total = sum(counts)
    if total <= 0:
        raise ValueError("пустая гистограмма")
    threshold = total * percent / 100.0
    cumulative = 0
    last_nonzero = 0
    for value, count in enumerate(counts):
        if not count:
            continue
        last_nonzero = value
        cumulative += count
        if cumulative >= threshold and cumulative > 0:
            return value
    return last_nonzero


def histogram_stats(counts):
    # type: (Sequence[int]) -> Dict[str, object]
    total = 0
    weighted = 0
    weighted_sq = 0
    minimum = None
    maximum = None
    distinct = 0
    for value, count in enumerate(counts):
        if not count:
            continue
        if minimum is None:
            minimum = value
        maximum = value
        distinct += 1
        total += count
        weighted += value * count
        weighted_sq += value * value * count
    if not total:
        raise ValueError("пустая гистограмма")
    mean = weighted / total
    variance = max(weighted_sq / total - mean * mean, 0.0)
    return {
        "pixels": total,
        "min": minimum,
        "max": maximum,
        "mean": round(mean, 2),
        "std": round(math.sqrt(variance), 2),
        "distinct_values": distinct,
        "effective_bits": max(1, int(maximum).bit_length()),
        "percentiles": {str(p): percentile_value(counts, p) for p in REPORT_PERCENTILES},
    }


def downsample(counts, bins):
    # type: (Sequence[int], int) -> List[int]
    """Сжать гистограмму до bins корзин (суммированием) — для графиков."""
    size = len(counts)
    step = max(1, size // bins)
    return [sum(counts[i:i + step]) for i in range(0, size, step)]
