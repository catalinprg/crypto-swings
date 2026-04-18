from src.fvg import detect_fvgs, FVG, MIN_GAP_ATR_MULT
from src.types import OHLC


def _b(ts, h, l, c=None):
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=c, high=h, low=l, close=c, volume=1.0)


def test_bullish_fvg_formed_when_bar_after_gaps_above_bar_before():
    # Classic 3-bar bull FVG: bar[0] high=100, bar[1] displacement,
    # bar[2] low=102 > bar[0] high.
    bars = [_b(0, 100, 98), _b(1, 103, 99, c=102.5), _b(2, 105, 102)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    bulls = [f for f in fvgs if f.type == "FVG_BULL"]
    assert len(bulls) == 1
    assert bulls[0].lo == 100  # bar[0].high
    assert bulls[0].hi == 102  # bar[2].low


def test_bearish_fvg_formed_when_bar_after_gaps_below_bar_before():
    bars = [_b(0, 105, 100), _b(1, 102, 95, c=97), _b(2, 98, 93)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    bears = [f for f in fvgs if f.type == "FVG_BEAR"]
    assert len(bears) == 1
    assert bears[0].hi == 100
    assert bears[0].lo == 98


def test_fvg_marked_mitigated_when_price_returns_into_gap():
    bars = [
        _b(0, 100, 98),  _b(1, 103, 99, c=102.5),  _b(2, 105, 102),
        _b(3, 106, 103), _b(4, 104, 100),   # bar[4] trades back into [100,102] gap
    ]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    assert any(f.type == "FVG_BULL" and f.mitigated for f in fvgs)


def test_fvg_stale_flag_triggers_past_threshold():
    # Construct an FVG at bar 2 then append 150 untouched bars far above the gap
    bars = [_b(0, 100, 98), _b(1, 103, 99, c=102.5), _b(2, 105, 102)]
    for i in range(3, 200):
        bars.append(_b(i, 110, 108))
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    bull = next(f for f in fvgs if f.type == "FVG_BULL")
    assert bull.stale is True
    assert bull.mitigated is False


def test_minimum_gap_atr_filter_suppresses_micro_gaps():
    # gap = nxt.low - prev.high = 0.01, atr_14 = 1.0
    # MIN_GAP_ATR_MULT * atr_14 = 0.05 → gap 0.01 is below threshold → filtered
    bars = [_b(0, 100.00, 98), _b(1, 103, 99, c=102.5), _b(2, 105, 100.01)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    bulls = [f for f in fvgs if f.type == "FVG_BULL"]
    assert len(bulls) == 0


def test_minimum_gap_atr_filter_passes_significant_gaps():
    # gap = 102 - 100 = 2.0, atr_14 = 1.0
    # MIN_GAP_ATR_MULT * atr_14 = 0.05 → gap 2.0 passes
    bars = [_b(0, 100, 98), _b(1, 103, 99, c=102.5), _b(2, 105, 102)]
    fvgs = detect_fvgs(bars, tf="1h", atr_14=1.0, stale_after=100)
    bulls = [f for f in fvgs if f.type == "FVG_BULL"]
    assert len(bulls) == 1
