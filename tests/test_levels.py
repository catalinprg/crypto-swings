from src.levels import cluster_levels, MultiSourceZone
from src.types import Level

def _lvl(price, source, tf="1d", strength=0.5, age=0, lo=None, hi=None):
    lo = lo if lo is not None else price
    hi = hi if hi is not None else price
    return Level(
        price=price, min_price=lo, max_price=hi,
        source=source, tf=tf, strength=strength, age_bars=age,
    )

def test_cluster_levels_groups_within_radius():
    levels = [
        _lvl(100.0, "FIB_618", tf="1d"),
        _lvl(100.2, "LIQ_BSL", tf="1d"),
        _lvl(102.0, "POC",     tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    assert len(zones) == 2
    assert zones[0].source_count == 2
    assert {"FIB_618", "LIQ_BSL"} <= {l.source for l in zones[0].levels}

def test_cluster_levels_score_rewards_source_diversity():
    # Two sources > three same-source hits
    two_src = cluster_levels([
        _lvl(100, "FIB_618", tf="1w"),
        _lvl(100, "LIQ_BSL", tf="1w"),
    ], radius=0.1)[0]
    three_same = cluster_levels([
        _lvl(100, "FIB_618", tf="1w"),
        _lvl(100, "FIB_618", tf="1d"),
        _lvl(100, "FIB_618", tf="4h"),
    ], radius=0.1)[0]
    assert two_src.score > three_same.score

def test_cluster_levels_empty_input_returns_empty_list():
    assert cluster_levels([], radius=1.0) == []

def test_cluster_levels_width_cap_splits_chain():
    # radius=0.5 → max_width=1.0. Four levels at 0.4 spacing each would chain
    # transitively, but the width cap must split at the point where the span
    # from group[0] exceeds 1.0.
    levels = [
        _lvl(100.0, "FIB_618", tf="1d"),
        _lvl(100.4, "LIQ_BSL", tf="1d"),
        _lvl(100.8, "POC",     tf="1d"),
        _lvl(101.2, "FVG_BULL",tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    # Width cap at 1.0 from 100.0 → break between 100.8 and 101.2.
    # Allow >=2 groups (implementation may split earlier on the exact boundary).
    assert len(zones) >= 2

def test_cluster_structural_pivot_classification():
    levels = [
        _lvl(100.0, "MS_BOS_LEVEL", tf="1d"),
        _lvl(100.05, "LIQ_BSL",     tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    assert len(zones) == 1
    assert zones[0].classification == "structural_pivot"


def test_all_avwap_variants_count_as_one_family():
    """Regression: AVWAP bands, swing-anchors, and event-anchors were
    previously split into 4 separate families, inflating zone classification
    and squeezing out the structural_pivot signal. All AVWAP_* tags must
    collapse to a single family."""
    # MS + every AVWAP variant — only 2 distinct families (MS + AVWAP),
    # so classification MUST be structural_pivot, not strong.
    levels = [
        _lvl(100.0, "MS_BOS_LEVEL",        tf="1d"),
        _lvl(100.1, "AVWAP_SESSION",       tf="1d"),
        _lvl(100.1, "AVWAP_WEEK",          tf="1d"),
        _lvl(100.1, "AVWAP_SWING_HH",      tf="1d"),
        _lvl(100.1, "AVWAP_BAND_1SD_UP",   tf="1d"),
        _lvl(100.1, "AVWAP_BAND_2SD_DOWN", tf="1d"),
        _lvl(100.1, "AVWAP_EVENT",         tf="1d"),
    ]
    zones = cluster_levels(levels, radius=0.5)
    assert len(zones) == 1
    assert zones[0].source_count == 2  # MS + AVWAP, that's it
    assert zones[0].classification == "structural_pivot"
