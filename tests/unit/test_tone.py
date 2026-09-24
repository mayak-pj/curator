import pytest

from tifjpg.imaging.base import ConversionOptions
from tifjpg.imaging.tone import LINEAR, ToneSpec, build_lut, window_from_histogram


def test_defaults_follow_decision_d12():
    options = ConversionOptions()
    assert options.tone.method == LINEAR
    assert not options.tone.needs_histogram
    assert options.quality == 100


def histogram(values, size=65536):
    counts = [0] * size
    for value in values:
        counts[value] += 1
    return counts


def test_linear_window_ignores_content():
    assert window_from_histogram(histogram([1000, 2000]), ToneSpec("linear")) == (0, 65535)


def test_minmax_window_is_data_range():
    assert window_from_histogram(histogram([1000, 1500, 2000]), ToneSpec("minmax")) == (1000, 2000)


def test_percentile_ignores_rare_outliers():
    values = list(range(1000, 2000)) * 100 + [0, 65535]
    lo, hi = window_from_histogram(histogram(values), ToneSpec("percentile", 0.5, 99.5))
    assert 1000 <= lo <= 1010
    assert 1990 <= hi <= 1999


def test_percentile_zero_to_hundred_equals_minmax():
    counts = histogram([5, 7, 7, 9, 300])
    assert window_from_histogram(counts, ToneSpec("percentile", 0.0, 100.0)) == \
        window_from_histogram(counts, ToneSpec("minmax"))


def test_exclude_extremes_ignores_saturated_pixels():
    values = [0] * 30 + [65535] * 30 + list(range(1000, 2000))
    counts = histogram(values)
    assert window_from_histogram(counts, ToneSpec("minmax")) == (0, 65535)
    assert window_from_histogram(counts, ToneSpec("minmax", exclude_extremes=True)) == (1000, 1999)


def test_exclude_extremes_falls_back_when_only_extremes():
    counts = histogram([0] * 5 + [65535] * 5)
    assert window_from_histogram(counts, ToneSpec("minmax", exclude_extremes=True)) == (0, 65535)


def test_flat_image_gets_non_empty_window():
    assert window_from_histogram(histogram([4000] * 10), ToneSpec("minmax")) == (4000, 4001)
    assert window_from_histogram(histogram([65535] * 10), ToneSpec("minmax")) == (65534, 65535)


def test_empty_histogram_rejected():
    with pytest.raises(ValueError):
        window_from_histogram([0] * 65536, ToneSpec("minmax"))


@pytest.mark.parametrize("method, p_low, p_high", [
    ("gamma", 0.5, 99.5),
    ("percentile", 50, 50),
    ("percentile", -1, 99),
    ("percentile", 1, 101),
])
def test_invalid_spec_rejected(method, p_low, p_high):
    with pytest.raises(ValueError):
        ToneSpec(method, p_low, p_high)


def test_lut_endpoints_and_clipping():
    lut = build_lut(1000, 2000)
    assert len(lut) == 65536
    assert lut[0] == lut[1000] == 0
    assert lut[2000] == lut[65535] == 255
    assert lut[1500] in (127, 128)


def test_lut_is_monotonic():
    lut = build_lut(123, 45678)
    assert all(a <= b for a, b in zip(lut, lut[1:]))


def test_linear_lut_matches_scaling():
    lut = build_lut(0, 65535)
    for value in (0, 128, 257, 32768, 65535):
        assert lut[value] == int(value * 255 / 65535 + 0.5)


def test_invalid_window_rejected():
    with pytest.raises(ValueError):
        build_lut(10, 10)
