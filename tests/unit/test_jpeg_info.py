import pytest

from tifjpg.imaging.jpeg_info import ZIGZAG, estimate_quality, ijg_table


@pytest.mark.parametrize("quality", [1, 10, 50, 75, 85, 90, 95, 99, 100])
def test_estimate_quality_recovers_ijg_table(quality):
    estimated, exact = estimate_quality(ijg_table(quality))
    assert exact
    assert ijg_table(estimated) == ijg_table(quality)


def test_quality_100_is_all_ones():
    assert ijg_table(100) == [1] * 64


def test_estimate_without_table():
    assert estimate_quality(None) == (None, False)


def test_zigzag_is_permutation():
    assert sorted(ZIGZAG) == list(range(64))
