import pytest
from src.fetch import parse_klines, TF_LOOKBACK

def test_parse_klines_maps_raw_binance_response():
    raw = [
        [1700000000000, "50000.00", "51000.00", "49500.00", "50500.00",
         "1000.5", 1700086399999, "x", 1, "x", "x", "x"],
    ]
    result = parse_klines(raw)
    assert len(result) == 1
    candle = result[0]
    assert candle.ts == 1700000000000
    assert candle.open == 50000.0
    assert candle.high == 51000.0
    assert candle.low == 49500.0
    assert candle.close == 50500.0
    assert candle.volume == 1000.5

def test_tf_lookback_has_all_five_timeframes():
    assert set(TF_LOOKBACK.keys()) == {"1M", "1w", "1d", "4h", "1h"}
    for v in TF_LOOKBACK.values():
        assert 1 <= v <= 1000
