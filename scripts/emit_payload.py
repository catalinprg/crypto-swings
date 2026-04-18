"""Emit the full analyst payload: unified confluence zones (fib + liq + VP +
AVWAP + FVG + OB + MS), plus all raw signal sections (derivatives, liquidity,
taker delta, structure bias).
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

from src import derivatives as derivatives_mod
from src import liquidity as liquidity_mod
from src.config import CONFIG
from src.fetch import fetch_all, taker_delta_per_tf
from src.venue_aggregator import fetch_all_venues, aggregate_bars
from src.fibs import compute_all
from src.main import (
    ATR_CLUSTER_MULTIPLIER, MAX_EXTENSION_DISTANCE_PCT,
    MIN_PAIRS_PER_TF, _latest,
)
from src.swings import atr, detect_swings, detect_pivots
from src.avwap import compute_avwap, resolve_anchors
from src.volume_profile import compute_profile, compute_naked_pocs
from src.fvg import detect_fvgs
from src.order_blocks import detect_order_blocks
from src.market_structure import analyze_structure
from src.levels import (
    cluster_levels, split_by_price,
    fibs_to_levels, pools_to_levels, profile_to_levels, naked_pocs_to_levels,
    avwap_to_levels, fvgs_to_levels, obs_to_levels, structure_to_levels,
)

MAX_LEVEL_DISTANCE_PCT = 0.20   # drop far-away levels before clustering
MAX_ZONES_PER_SIDE = 10         # cap payload size


async def _aggregated_per_tf(symbol: str, binance_ohlc: dict) -> tuple[dict, list[str]]:
    """For each TF: fetch Bybit + Coinbase bars and aggregate with Binance.
    Returns (agg_ohlc, venues_used_union) — venues_used reflects actual
    non-empty coverage across any TF, not a hardcoded list."""
    agg: dict = {}
    venues_seen: set[str] = set()
    if any(binance_ohlc.values()):
        venues_seen.add("binance")
    for tf, binance_bars in binance_ohlc.items():
        others = await fetch_all_venues(symbol, tf, limit=len(binance_bars))
        agg[tf] = aggregate_bars({
            "binance": binance_bars,
            "bybit":    others["bybit"],
            "coinbase": others["coinbase"],
        })
        if others["bybit"]:
            venues_seen.add("bybit")
        if others["coinbase"]:
            venues_seen.add("coinbase")
    return agg, sorted(venues_seen)


async def build() -> dict:
    ohlc, deriv = await asyncio.gather(fetch_all(), derivatives_mod.fetch_all())

    # --- Swings + fibs (Binance-only as before; swings need historic stability)
    all_pairs = []
    contributing, skipped = [], []
    for tf, bars in ohlc.items():
        pairs = detect_swings(bars, tf=tf, max_pairs=3)
        if len(pairs) < MIN_PAIRS_PER_TF:
            skipped.append(tf)
            continue
        all_pairs.extend(pairs)
        contributing.append(tf)

    daily_bars = ohlc["1d"]
    current_price = daily_bars[-1].close
    daily_atr = _latest(atr(daily_bars, 14))
    radius = daily_atr * ATR_CLUSTER_MULTIPLIER

    fibs = compute_all(all_pairs)
    fibs = [
        l for l in fibs
        if l.kind == "retracement"
        or abs(l.price - current_price) / current_price <= MAX_EXTENSION_DISTANCE_PCT
    ]

    # --- Liquidity pools (unchanged)
    liquidity_pools = liquidity_mod.compute_pools(
        swing_pairs=all_pairs, ohlc=ohlc,
        current_price=current_price, daily_atr=daily_atr,
    )

    # --- Aggregated OHLCV for VP / AVWAP only
    agg_ohlc, venues_used = await _aggregated_per_tf(CONFIG.symbol, ohlc)

    # --- Volume Profile per TF (on aggregated bars)
    vp_by_tf: dict = {}
    for tf, bars in agg_ohlc.items():
        try:
            tf_atr = _latest(atr(bars, 14))
        except RuntimeError:
            continue   # insufficient bars for ATR; skip this TF
        vp_by_tf[tf] = compute_profile(bars, atr_14=tf_atr)

    # --- Naked POCs (daily / weekly / monthly) on aggregated 1h bars
    naked_pocs: dict[str, list] = {"D": [], "W": [], "M": []}
    if "1h" in agg_ohlc and agg_ohlc["1h"]:
        h1 = agg_ohlc["1h"]
        daily_atr_for_pocs = daily_atr
        naked_pocs["D"] = compute_naked_pocs(h1, period_ms=86_400_000,    lookback=10, atr_14=daily_atr_for_pocs)
        naked_pocs["W"] = compute_naked_pocs(h1, period_ms=7*86_400_000,  lookback=6,  atr_14=daily_atr_for_pocs)
        naked_pocs["M"] = compute_naked_pocs(h1, period_ms=30*86_400_000, lookback=3,  atr_14=daily_atr_for_pocs)

    # --- AVWAP per TF (on aggregated bars)
    avwap_by_tf: dict = {}
    for tf, bars in agg_ohlc.items():
        if not bars:
            continue
        anchors = resolve_anchors(bars, [p for p in all_pairs if p.tf == tf])
        avwap_by_tf[tf] = [
            compute_avwap(bars, anchor_idx=idx, anchor_type=typ, anchor_ts=ts)
            for typ, idx, ts in anchors
        ]

    # --- FVG / OB / MS per TF (Binance bars — stable reference)
    fvg_by_tf: dict = {}
    ob_by_tf: dict = {}
    ms_by_tf: dict = {}
    for tf, bars in ohlc.items():
        # MS first — doesn't depend on ATR (detect_pivots computes its own).
        highs, lows = detect_pivots(bars, n=None)
        ms_by_tf[tf] = analyze_structure(highs, lows, current_price=current_price)

        # FVG + OB need ATR; skip if series too short.
        try:
            tf_atr = _latest(atr(bars, 14))
        except RuntimeError:
            continue
        fvg_by_tf[tf] = detect_fvgs(bars, tf=tf, atr_14=tf_atr)
        ob_by_tf[tf]  = detect_order_blocks(bars, tf=tf, atr_14=tf_atr)

    # --- Unified level list
    levels = fibs_to_levels(fibs)
    levels += pools_to_levels(liquidity_pools, tf="1d")
    for tf, vp in vp_by_tf.items():
        levels += profile_to_levels(vp, tf=tf)
    for period, pocs in naked_pocs.items():
        levels += naked_pocs_to_levels(pocs, period=period, tf="1d")
    for tf, avwaps in avwap_by_tf.items():
        levels += avwap_to_levels(avwaps, tf=tf)
    for fvgs in fvg_by_tf.values():
        levels += fvgs_to_levels(fvgs)
    for obs in ob_by_tf.values():
        levels += obs_to_levels(obs)
    for tf, ms in ms_by_tf.items():
        levels += structure_to_levels(ms, tf=tf)

    # Drop levels beyond ±20% of current price
    levels = [l for l in levels if abs(l.price - current_price) / current_price <= MAX_LEVEL_DISTANCE_PCT]

    zones = cluster_levels(levels, radius=radius)
    support, resistance = split_by_price(zones, current_price)

    # --- Payload
    prev_close = daily_bars[-2].close
    change_24h_pct = (current_price - prev_close) / prev_close * 100

    def z_to_dict(z):
        return {
            "min_price": round(z.min_price, 2),
            "max_price": round(z.max_price, 2),
            "mid": round(z.mid, 2),
            "score": z.score,
            "source_count": z.source_count,
            "classification": z.classification,
            "distance_pct": round((z.mid - current_price) / current_price * 100, 2),
            "sources": sorted({l.source for l in z.levels}),
            "contributing_levels": sorted(
                [{"source": l.source, "tf": l.tf, "price": round(l.price, 2), "meta": l.meta}
                 for l in z.levels],
                key=lambda d: d["price"],
            ),
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
        "resistance": [z_to_dict(z) for z in resistance[:MAX_ZONES_PER_SIDE]],
        "support":    [z_to_dict(z) for z in support[:MAX_ZONES_PER_SIDE]],
        "derivatives": deriv,
        "spot_taker_delta_by_tf": taker_delta_per_tf(ohlc),
        "liquidity": liquidity_pools,
        "market_structure": {
            tf: {
                "bias": ms.bias,
                "last_bos": ms.last_bos,
                "last_choch": ms.last_choch,
                "invalidation_level": ms.invalidation_level,
            }
            for tf, ms in ms_by_tf.items()
        },
        "naked_pocs": {
            period: [
                {
                    "price": round(p.price, 2),
                    "period_start_ts": p.period_start_ts,
                    "period_end_ts": p.period_end_ts,
                    "distance_atr": round(p.distance_atr, 2),
                }
                for p in lst if p.is_naked
            ]
            for period, lst in naked_pocs.items()
        },
        "venue_sources": venues_used,
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
