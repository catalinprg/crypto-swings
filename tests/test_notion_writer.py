from src.types import SwingPair, FibLevel, Zone
from src.notion_writer import build_page_payload

def _zone(price, score, n_levels=2):
    pair = SwingPair(tf="1d", high_price=price+10, high_ts=1,
                     low_price=price-10, low_ts=0, direction="up")
    lvls = tuple(
        FibLevel(price=price, tf="1d", ratio=0.618, kind="retracement", pair=pair)
        for _ in range(n_levels)
    )
    return Zone(min_price=price, max_price=price, score=score, levels=lvls)

def test_payload_has_title_with_timestamp():
    payload = build_page_payload(
        current_price=60000.0,
        change_24h_pct=2.5,
        atr_daily=800.0,
        support=[_zone(58000, 10)],
        resistance=[_zone(62000, 12)],
        contributing_tfs=["1M", "1w", "1d"],
        skipped_tfs=["4h"],
        parent_page_id="345b7f28c0448048b7dce766dada1c29",
    )
    assert "BTC Swings" in payload["title"]
    assert "UTC" in payload["title"]

def test_payload_includes_support_and_resistance_sections():
    payload = build_page_payload(
        current_price=60000.0, change_24h_pct=2.5, atr_daily=800.0,
        support=[_zone(58000, 10)],
        resistance=[_zone(62000, 12)],
        contributing_tfs=["1M","1w","1d","4h","1h"], skipped_tfs=[],
        parent_page_id="x",
    )
    body = payload["body"]
    assert "Resistance" in body
    assert "Support" in body
    assert "62000" in body or "62,000" in body
    assert "58000" in body or "58,000" in body

def test_payload_flags_skipped_timeframes():
    payload = build_page_payload(
        current_price=60000.0, change_24h_pct=0.0, atr_daily=800.0,
        support=[], resistance=[],
        contributing_tfs=["1M","1w","1d"], skipped_tfs=["4h","1h"],
        parent_page_id="x",
    )
    assert "4h" in payload["body"] and "1h" in payload["body"]
    assert "skipped" in payload["body"].lower()

def test_payload_carries_parent_page_id():
    payload = build_page_payload(
        current_price=60000.0, change_24h_pct=0.0, atr_daily=800.0,
        support=[], resistance=[],
        contributing_tfs=["1d"], skipped_tfs=[],
        parent_page_id="345b7f28c0448048b7dce766dada1c29",
    )
    assert payload["parent_page_id"] == "345b7f28c0448048b7dce766dada1c29"
