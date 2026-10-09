from curator.imaging.histogram import downsample, histogram_stats, percentile_value


def test_stats_of_small_histogram():
    counts = [0] * 16
    counts[2] = 1
    counts[4] = 2
    counts[10] = 1
    stats = histogram_stats(counts)
    assert stats["pixels"] == 4
    assert stats["min"] == 2
    assert stats["max"] == 10
    assert stats["mean"] == 5.0
    assert stats["distinct_values"] == 3
    assert stats["effective_bits"] == 4


def test_percentile_value():
    counts = [0] * 10
    counts[1] = 50
    counts[9] = 50
    assert percentile_value(counts, 50) == 1
    assert percentile_value(counts, 51) == 9
    assert percentile_value(counts, 100) == 9


def test_downsample_preserves_total():
    counts = list(range(64))
    assert sum(downsample(counts, 8)) == sum(counts)
    assert len(downsample(counts, 8)) == 8
