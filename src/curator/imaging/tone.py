"""Перевод 16-битных значений в 8-битные (ARCHITECTURE.md, 8.3).

Чистый Python: окно [lo, hi] вычисляется по гистограмме, затем строится
таблица подстановки (LUT) на все 65536 значений. Применение LUT к
изображению выполняет конвертер — так результат точен, детерминирован и
тестируется без libvips.
"""

from dataclasses import dataclass
from typing import Sequence, Tuple

LINEAR = "linear"
MINMAX = "minmax"
PERCENTILE = "percentile"
METHODS = (LINEAR, MINMAX, PERCENTILE)


@dataclass(frozen=True)
class ToneSpec:
    """Способ перевода в 8 бит.

    linear     — весь диапазон 0..max без учёта содержимого снимка;
    minmax     — от минимального до максимального значения снимка;
    percentile — от перцентиля p_low до перцентиля p_high (устойчив к выбросам).

    exclude_extremes (для minmax/percentile) — не учитывать пиксели, равные
    0 и максимуму: у снимков, уже обрезанных аппаратом (Diada64 насыщает
    по 3 % с каждой стороны), иначе окно всегда совпадает с linear.
    """

    method: str
    p_low: float = 0.5
    p_high: float = 99.5
    exclude_extremes: bool = False

    def __post_init__(self):
        if self.method not in METHODS:
            raise ValueError("неизвестный метод тон-маппинга: {!r}".format(self.method))
        if not 0.0 <= self.p_low < self.p_high <= 100.0:
            raise ValueError(
                "перцентили должны удовлетворять 0 <= p_low < p_high <= 100, "
                "получено {} и {}".format(self.p_low, self.p_high)
            )

    @property
    def needs_histogram(self):
        return self.method != LINEAR

    def describe(self):
        if self.method == PERCENTILE:
            text = "percentile {:g}–{:g} %".format(self.p_low, self.p_high)
        else:
            text = self.method
        if self.exclude_extremes and self.method != LINEAR:
            text += " (без 0/max)"
        return text


# Решение D12 (ARCHITECTURE.md): снимки уже растянуты аппаратом, окно не пересчитываем.
DEFAULT_TONE = ToneSpec(LINEAR)


def window_from_histogram(counts, spec):
    # type: (Sequence[int], ToneSpec) -> Tuple[int, int]
    """Окно [lo, hi] по гистограмме, где counts[v] — число пикселей со значением v."""
    size = len(counts)
    if size < 2:
        raise ValueError("гистограмма должна содержать минимум 2 корзины")
    max_value = size - 1
    if spec.method == LINEAR:
        return 0, max_value

    if sum(counts) <= 0:
        raise ValueError("пустая гистограмма")
    if spec.exclude_extremes:
        inner = list(counts)
        inner[0] = 0
        inner[max_value] = 0
        if sum(inner) > 0:
            counts = inner
    total = sum(counts)

    if spec.method == MINMAX:
        p_low, p_high = 0.0, 100.0
    else:
        p_low, p_high = spec.p_low, spec.p_high
    low_threshold = total * p_low / 100.0
    high_threshold = total * p_high / 100.0

    lo = None
    hi = max_value
    cumulative = 0
    for value, count in enumerate(counts):
        if not count:
            continue
        cumulative += count
        if lo is None and cumulative > low_threshold:
            lo = value
        if cumulative >= high_threshold:
            hi = value
            break
    if lo is None:
        lo = hi

    if hi <= lo:
        # Однотонный снимок или слишком узкое окно: избегаем деления на ноль.
        if lo < max_value:
            hi = lo + 1
        else:
            lo = hi - 1
    return lo, hi


def build_lut(lo, hi, size=65536):
    # type: (int, int, int) -> bytes
    """LUT: v <= lo -> 0, v >= hi -> 255, между ними — линейно с округлением."""
    if not 0 <= lo < hi < size:
        raise ValueError("некорректное окно [{}, {}] для LUT размера {}".format(lo, hi, size))
    span = hi - lo
    lut = bytearray(size)
    for value in range(lo + 1, hi):
        lut[value] = ((value - lo) * 510 + span) // (2 * span)
    for value in range(hi, size):
        lut[value] = 255
    return bytes(lut)
