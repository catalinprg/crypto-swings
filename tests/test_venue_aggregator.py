from src.venue_aggregator import aggregate_bars
from src.types import OHLC


def _b(ts, o, h, l, c, v):
    return OHLC(ts=ts, open=o, high=h, low=l, close=c, volume=v)


def test_aggregate_bars_sums_volume_across_venues():
    binance = [_b(1000, 100, 101, 99, 100.5, 10.0)]
    bybit   = [_b(1000, 100, 101, 99, 100.5,  3.0)]
    coinbase= [_b(1000, 100, 101, 99, 100.5,  2.0)]
    out = aggregate_bars({"binance": binance, "bybit": bybit, "coinbase": coinbase})
    assert len(out) == 1
    assert out[0].ts == 1000
    assert out[0].volume == 15.0
    # OHLC taken from the PRIMARY (binance) for reference-price stability
    assert out[0].close == 100.5


def test_aggregate_bars_uses_union_of_timestamps():
    binance = [_b(1000, 100, 101, 99, 100.5, 10.0)]
    bybit   = [_b(2000, 101, 102, 100, 101.5, 5.0)]
    out = aggregate_bars({"binance": binance, "bybit": bybit})
    timestamps = [b.ts for b in out]
    assert timestamps == [1000, 2000]


def test_aggregate_bars_missing_primary_falls_back_to_first_present():
    bybit = [_b(1000, 100, 101, 99, 100.5, 3.0)]
    out = aggregate_bars({"binance": [], "bybit": bybit})
    assert len(out) == 1
    assert out[0].volume == 3.0


from src.venue_aggregator import resample


def test_resample_1h_to_4h():
    # 4 consecutive 1h bars → one 4h bar
    bars = [
        _b(0,            100, 105, 99,  103, 10.0),
        _b(3_600_000,    103, 108, 102, 107, 12.0),
        _b(7_200_000,    107, 110, 106, 109, 15.0),
        _b(10_800_000,   109, 112, 108, 111, 8.0),
    ]
    out = resample(bars, "1h", "4h")
    assert len(out) == 1
    assert out[0].open == 100
    assert out[0].high == 112
    assert out[0].low == 99
    assert out[0].close == 111
    assert out[0].volume == 45.0
