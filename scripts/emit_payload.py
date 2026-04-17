"""Run the full pipeline and emit a payload JSON file for the analyst agent.

Usage:
    ASSET=btc uv run python -m scripts.emit_payload [output_path]
    ASSET=eth uv run python -m scripts.emit_payload [output_path]

Default output: /tmp/{asset}_swings_payload.json

Requires COINALYZE_API_KEY env var for the derivatives layer (optional —
degrades to status=unavailable if missing).
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

from src import derivatives as derivatives_mod
from src.config import CONFIG
from src.confluence import cluster, split_by_price
from src.fetch import fetch_all
from src.fibs import compute_all
from src.main import (
    ATR_CLUSTER_MULTIPLIER,
    MAX_EXTENSION_DISTANCE_PCT,
    MIN_PAIRS_PER_TF,
    _latest,
)
from src.swings import atr, detect_swings


async def build():
    ohlc, deriv = await asyncio.gather(fetch_all(), derivatives_mod.fetch_all())
    all_pairs = []
    contributing, skipped = [], []
    for tf, bars in ohlc.items():
        pairs = detect_swings(bars, tf=tf, max_pairs=3)
        if len(pairs) < MIN_PAIRS_PER_TF:
            skipped.append(tf)
            continue
        all_pairs.extend(pairs)
        contributing.append(tf)

    levels = compute_all(all_pairs)
    daily_bars = ohlc["1d"]
    current_price = daily_bars[-1].close
    levels = [
        l for l in levels
        if l.kind == "retracement"
        or abs(l.price - current_price) / current_price <= MAX_EXTENSION_DISTANCE_PCT
    ]
    daily_atr = _latest(atr(daily_bars, 14))
    radius = daily_atr * ATR_CLUSTER_MULTIPLIER
    zones = cluster(levels, radius=radius)
    prev_close = daily_bars[-2].close
    change_24h_pct = (current_price - prev_close) / prev_close * 100
    support, resistance = split_by_price(zones, current_price)

    def z_to_dict(z):
        mid = (z.min_price + z.max_price) / 2
        return {
            "min_price": round(z.min_price, 2),
            "max_price": round(z.max_price, 2),
            "score": z.score,
            "distance_pct": round((mid - current_price) / current_price * 100, 2),
            "contributing_levels": sorted({f"{l.tf} {l.ratio}" for l in z.levels}),
        }

    if deriv.get("status") == "ok" and deriv.get("liquidation_clusters_72h"):
        deriv["liquidation_clusters_72h"] = derivatives_mod.enrich_clusters_with_price(
            deriv["liquidation_clusters_72h"], ohlc["4h"]
        )

    return {
        "asset": CONFIG.asset,
        "display_name": CONFIG.display_name,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_price": round(current_price, 2),
        "change_24h_pct": round(change_24h_pct, 2),
        "daily_atr": round(daily_atr, 2),
        "contributing_tfs": contributing,
        "skipped_tfs": skipped,
        "resistance": [z_to_dict(z) for z in resistance[:8]],
        "support": [z_to_dict(z) for z in support[:8]],
        "derivatives": deriv,
    }


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else f"/tmp/{CONFIG.asset}_swings_payload.json"
    payload = asyncio.run(build())
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"payload written: {out_path}")
    print(f"current: {payload['current_price']} "
          f"resistance: {len(payload['resistance'])} "
          f"support: {len(payload['support'])} "
          f"derivatives: {payload['derivatives']['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
