from src.market_structure import analyze_structure, StructureState
from src.swings import detect_pivots
from src.types import OHLC


def _b(ts, h, l, c=None):
    c = c if c is not None else (h + l) / 2
    return OHLC(ts=ts, open=c, high=h, low=l, close=c, volume=1.0)


# --- Spec tests ---

def test_uptrend_classified_as_bullish():
    # HH then HL then HH: classic uptrend
    # Pivots: highs rising, lows rising.
    pivots_highs = [(1, 100.0), (3, 105.0), (5, 110.0)]
    pivots_lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=109.0)
    assert state.bias == "bullish"
    assert state.last_bos is not None
    assert state.invalidation_level is not None  # the most recent HL


def test_choch_on_first_break_against_trend():
    # Uptrend then price breaks below the most recent higher low → CHoCH bearish
    pivots_highs = [(1, 100.0), (3, 105.0)]
    pivots_lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=101.0)
    # price 101 < most recent HL 102 → CHoCH bearish
    assert state.last_choch is not None
    assert state.last_choch["direction"] == "bearish"


# --- Extra tests ---

def test_bearish_mirror_bos():
    # LH + LL sequence, current price breaks below LL → bearish BOS
    pivots_highs = [(1, 110.0), (3, 105.0), (5, 100.0)]
    pivots_lows  = [(2, 98.0),  (4, 94.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=93.0)
    assert state.bias == "bearish"
    assert state.last_bos is not None
    assert state.last_bos["direction"] == "bearish"
    assert state.last_choch is None


def test_bearish_mirror_choch():
    # LH + LL sequence, current price breaks above most recent LH → bullish CHoCH
    pivots_highs = [(1, 110.0), (3, 105.0), (5, 100.0)]
    pivots_lows  = [(2, 98.0),  (4, 94.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=101.0)
    assert state.bias == "bearish"
    assert state.last_choch is not None
    assert state.last_choch["direction"] == "bullish"
    # BOS is always set for a trending market (last confirmed LL); CHoCH coexists.
    assert state.last_bos is not None


def test_range_expanding_volatility():
    # Last 2 highs: HH (100 → 110). Last 2 lows: LL (90 → 88).
    # Expanding range — neither clean bullish (needs HL) nor bearish (needs LH) → range.
    pivots_highs = [(1, 100.0), (3, 110.0)]
    pivots_lows  = [(2, 90.0),  (4, 88.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=103.0)
    assert state.bias == "range"
    assert state.last_bos is None
    assert state.last_choch is None
    assert state.invalidation_level is None


def test_range_compressing_wedge():
    # Last 2 highs: LH (110 → 105). Last 2 lows: HL (90 → 95).
    # Compressing wedge — neither bullish nor bearish → range.
    pivots_highs = [(1, 110.0), (3, 105.0)]
    pivots_lows  = [(2, 90.0),  (4, 95.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=100.0)
    assert state.bias == "range"


def test_last_two_pivots_override_historical_outlier():
    """Regression: bias should reflect the MOST RECENT swing, not history-wide
    monotonicity. An old outlier pivot must not void a current trend shift."""
    # Highs: outlier at 105, then 100, then 110. Last 2: 100→110 (HH).
    # Lows: 95 → 102 (HL).
    # Should be bullish despite the non-monotonic high sequence.
    pivots_highs = [(1, 105.0), (3, 100.0), (5, 110.0)]
    pivots_lows  = [(2, 95.0),  (4, 102.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=108.0)
    assert state.bias == "bullish"
    assert state.last_bos is not None
    assert state.last_bos["level"] == 110.0   # most recent HH
    assert state.invalidation_level == 102.0  # most recent HL


def test_insufficient_pivots_returns_range():
    # <2 highs or <2 lows → bias=range, everything None
    state = analyze_structure([(1, 100.0)], [(2, 90.0), (4, 88.0)], current_price=95.0)
    assert state.bias == "range"
    assert state.last_bos is None
    assert state.last_choch is None
    assert state.invalidation_level is None

    state2 = analyze_structure([(1, 100.0), (3, 105.0)], [(2, 90.0)], current_price=95.0)
    assert state2.bias == "range"
    assert state2.last_bos is None
    assert state2.last_choch is None
    assert state2.invalidation_level is None


def test_bullish_price_in_middle_no_choch():
    # Uptrend, price between HL and HH — BOS is the confirmed HH, no CHoCH yet.
    pivots_highs = [(1, 100.0), (3, 105.0), (5, 110.0)]
    pivots_lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=106.0)
    assert state.bias == "bullish"
    assert state.last_bos is not None         # confirmed HH=110 is the last BOS
    assert state.last_bos["level"] == 110.0
    assert state.last_choch is None
    assert state.invalidation_level == 102.0


def test_bos_dict_keys():
    # last_bos contains "direction", "level", "ts"
    pivots_highs = [(1, 100.0), (3, 105.0), (5, 110.0)]
    pivots_lows  = [(2, 98.0),  (4, 102.0)]
    state = analyze_structure(pivots_highs, pivots_lows, current_price=111.0)
    assert state.last_bos is not None
    assert set(state.last_bos.keys()) == {"direction", "level", "ts"}
    assert state.last_bos["direction"] == "bullish"
    assert state.last_bos["level"] == 110.0
    assert state.last_bos["ts"] == 5
