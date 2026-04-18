from src.volume_profile import compute_profile, compute_naked_pocs
from src.types import OHLC

def _b(ts, h, l, v, c=None):
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=c, high=h, low=l, close=c, volume=v)

def test_compute_profile_finds_poc_at_concentration():
    # 10 bars all at price 100 with 1 unit vol; one bar at 110 with 50 units.
    # POC must be at 110.
    bars = [_b(i, 101, 99, 1.0) for i in range(10)]
    bars.append(_b(11, 111, 109, 50.0))
    profile = compute_profile(bars, atr_14=2.0)
    assert 109 <= profile.poc <= 111

def test_compute_profile_value_area_brackets_poc():
    bars = [_b(i, 101, 99, 10.0) for i in range(20)]
    profile = compute_profile(bars, atr_14=2.0)
    assert profile.val <= profile.poc <= profile.vah

def test_naked_poc_flagged_when_price_never_returned():
    # First 10 bars form a daily window with POC ~100
    # Next 10 bars trade entirely above 105 (never back to POC)
    day1 = [_b(i, 101, 99, 10.0)          for i in range(10)]
    day2 = [_b(10 + i, 110, 106, 10.0)    for i in range(10)]
    all_bars = day1 + day2
    pocs = compute_naked_pocs(all_bars, period_ms=10, lookback=2, atr_14=2.0)
    # The oldest period (period_start_ts < the day2 window start) should be naked.
    day1_poc = min(pocs, key=lambda p: p.period_start_ts)
    assert day1_poc.is_naked

# --- Extra coverage per code review requirements ---

def test_compute_profile_empty_bars_returns_zero_profile():
    profile = compute_profile([], atr_14=2.0)
    assert profile.poc == 0
    assert profile.vah == 0
    assert profile.val == 0
    assert profile.hvn == []
    assert profile.lvn == []
    assert profile.bin_width == 0

def test_compute_naked_pocs_empty_bars_returns_empty():
    result = compute_naked_pocs([], period_ms=10, lookback=2, atr_14=2.0)
    assert result == []

def test_compute_naked_pocs_all_post_bars_visit_poc():
    # day1 POC ~100; day2 bars revisit 100 explicitly
    day1 = [_b(i, 101, 99, 10.0) for i in range(10)]
    # day2 bars trade through 99-111, so they cross over 100
    day2 = [_b(10 + i, 111, 99, 10.0) for i in range(10)]
    all_bars = day1 + day2
    pocs = compute_naked_pocs(all_bars, period_ms=10, lookback=2, atr_14=2.0)
    # day1's POC (~100) was revisited — none of the returned POCs for that period should be naked
    for p in pocs:
        if p.period_start_ts < 10:
            assert not p.is_naked
