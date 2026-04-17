import json
from pathlib import Path
import pytest
from src.derivatives import (
    aggregate_open_interest, aggregate_liquidations,
    detect_clusters, build_derivatives_payload, enrich_clusters_with_price,
)
from src.types import OHLC

FIX = Path(__file__).parent / "fixtures" / "coinalyze"

def _load(name):
    return json.loads((FIX / f"{name}.json").read_text())

def test_aggregate_open_interest_sums_across_venues():
    raw_current = [
        {"symbol": "BTCUSDT_PERP.A", "value": 7_000_000_000.0, "update": 1},
        {"symbol": "BTCUSDT.6",      "value": 4_000_000_000.0, "update": 1},
        {"symbol": "BTCUSDT_PERP.3", "value": 2_000_000_000.0, "update": 1},
    ]
    raw_history = [
        {"symbol": "BTCUSDT_PERP.A", "history": [
            {"t": 1000, "o": 6_500_000_000, "h": 0, "l": 0, "c": 6_800_000_000},
            {"t": 1001, "o": 6_800_000_000, "h": 0, "l": 0, "c": 7_000_000_000},
        ]},
        {"symbol": "BTCUSDT.6", "history": [
            {"t": 1000, "o": 3_800_000_000, "h": 0, "l": 0, "c": 3_900_000_000},
            {"t": 1001, "o": 3_900_000_000, "h": 0, "l": 0, "c": 4_000_000_000},
        ]},
        # OKX history missing — must not crash
    ]
    result = aggregate_open_interest(raw_current, raw_history, lookback_buckets=1)
    assert result["total_usd"] == 13_000_000_000.0
    # 24h-ago total across available venues: 6.8B + 3.9B = 10.7B
    # current-matching total (same venues only): 7B + 4B = 11B
    # pct change: (11B - 10.7B) / 10.7B * 100 ≈ 2.80%
    assert round(result["change_24h_pct"], 2) == 2.80
    assert set(result["venues_used"]) == {"A", "6"}


def test_aggregate_liquidations_24h_totals():
    raw_liq = [
        {"symbol": "BTCUSDT_PERP.A", "history": [
            {"t": t, "l": 10_000_000.0, "s": 2_000_000.0}
            for t in range(1000, 1018)  # 18 buckets
        ]},
    ]
    result = aggregate_liquidations(raw_liq, buckets_24h=6)
    # Last 6 buckets: 6*10M long, 6*2M short
    assert result["long_usd"] == 60_000_000.0
    assert result["short_usd"] == 12_000_000.0
    assert result["dominant_side"] == "long"

def test_detect_clusters_flags_outlier_buckets():
    # 17 buckets with ~1M total liq, 1 bucket with 100M total
    history = [{"t": 1100 + i, "l": 500_000, "s": 500_000} for i in range(17)]
    history.insert(10, {"t": 1010, "l": 50_000_000, "s": 50_000_000})
    raw = [{"symbol": "BTCUSDT_PERP.A", "history": history}]
    clusters = detect_clusters(raw, stddev_threshold=2.0)
    assert len(clusters) >= 1
    assert clusters[0]["total_usd"] == 100_000_000
    assert clusters[0]["t"] == 1010

def test_build_derivatives_payload_happy_path_from_fixtures():
    payload = build_derivatives_payload(
        open_interest_raw=_load("open_interest"),
        open_interest_history_raw=_load("open_interest_history"),
        liquidations_raw=_load("liquidation_history"),
        funding={"rate_8h_pct": 0.002, "annualized_pct": 2.19},
    )
    assert payload["status"] == "ok"
    assert payload["open_interest_usd"] > 1_000_000_000
    assert payload["funding_rate_annualized_pct"] is not None
    assert "long_usd" in payload["liquidations_24h"]
    assert isinstance(payload["liquidation_clusters_72h"], list)

def test_build_derivatives_payload_handles_empty_inputs():
    payload = build_derivatives_payload(
        open_interest_raw=[],
        open_interest_history_raw=[],
        liquidations_raw=[],
        funding=None,
    )
    assert payload["status"] == "unavailable"


def test_build_derivatives_payload_degrades_when_oi_is_missing():
    # Simulates Coinalyze 503 on /open-interest while liquidations + funding
    # still came back. Must not discard the surviving sections.
    payload = build_derivatives_payload(
        open_interest_raw=[],
        open_interest_history_raw=[],
        liquidations_raw=_load("liquidation_history"),
        funding={"rate_8h_pct": 0.01, "annualized_pct": 10.95},
    )
    assert payload["status"] == "ok"
    assert payload["partial"] is True
    assert "oi" in payload["missing_sections"]
    assert payload["open_interest_usd"] is None
    assert payload["open_interest_change_24h_pct"] is None
    assert payload["funding_rate_annualized_pct"] == 10.95
    assert payload["liquidations_24h"] is not None
    assert "long_usd" in payload["liquidations_24h"]


def _bar(ts_ms, high, low, close):
    return OHLC(ts=ts_ms, open=close, high=high, low=low, close=close, volume=0)


def test_enrich_clusters_attaches_price_from_matching_4h_bar():
    # 4h bar at t=1000s, spans [1000s, 1000s + 4h) in ms terms
    bars = [
        _bar(1000 * 1000, high=70000, low=69000, close=69500),
        _bar((1000 + 4 * 3600) * 1000, high=71000, low=70500, close=70800),
    ]
    clusters = [
        {"t": 1000, "total_usd": 50_000_000, "dominant_side": "long"},
        {"t": 1000 + 4 * 3600 + 100, "total_usd": 30_000_000, "dominant_side": "short"},
    ]
    enriched = enrich_clusters_with_price(clusters, bars)
    assert enriched[0]["price_high"] == 70000
    assert enriched[0]["price_low"] == 69000
    assert enriched[0]["price_close"] == 69500
    assert enriched[1]["price_high"] == 71000
    assert enriched[1]["price_low"] == 70500


def test_enrich_clusters_leaves_unmatched_as_none():
    bars = [_bar(1000 * 1000, high=70000, low=69000, close=69500)]
    # Cluster timestamp far outside the bar window
    clusters = [{"t": 99999999, "total_usd": 10_000_000, "dominant_side": "long"}]
    enriched = enrich_clusters_with_price(clusters, bars)
    assert enriched[0]["price_high"] is None
    assert enriched[0]["price_low"] is None
    assert enriched[0]["price_close"] is None
    # Original fields preserved
    assert enriched[0]["total_usd"] == 10_000_000
