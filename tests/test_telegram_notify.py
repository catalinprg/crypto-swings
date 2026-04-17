from src.types import SwingPair, FibLevel, Zone
from src.telegram_notify import build_summary


def _zone(price, score):
    pair = SwingPair(tf="1d", high_price=price+10, high_ts=1,
                     low_price=price-10, low_ts=0, direction="up")
    lvls = (FibLevel(price=price, tf="1d", ratio=0.618,
                     kind="retracement", pair=pair),)
    return Zone(min_price=price, max_price=price, score=score, levels=lvls)


def test_summary_includes_current_price_and_top_zones():
    msg = build_summary(
        current_price=60000.0,
        top_support=[_zone(58000, 10), _zone(55000, 8)],
        top_resistance=[_zone(62000, 12), _zone(65000, 9)],
        notion_url="https://notion.so/page-abc",
    )
    assert "60" in msg
    assert "58" in msg and "62" in msg
    assert "notion.so/page-abc" in msg


def test_summary_handles_empty_zones():
    msg = build_summary(
        current_price=60000.0,
        top_support=[],
        top_resistance=[],
        notion_url="https://notion.so/page-abc",
    )
    assert "60" in msg
    assert "notion.so/page-abc" in msg
