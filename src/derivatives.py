"""Coinalyze derivatives enrichment.

Env var (required for `fetch_all`, not for pure-function aggregators):
  COINALYZE_API_KEY

Fetches Coinalyze endpoints (OI, liquidations) in parallel across three
USDT-margined perps (Binance, Bybit, OKX) for the active asset, and funding
rate from Bybit's public tickers endpoint. Aggregates client-side and
returns a single derivatives dict for the pipeline payload. Gracefully
degrades on failure or partial venue coverage.
"""
import asyncio
import os
import statistics
import time
from typing import Any

import httpx

from src.config import CONFIG

COINALYZE_BASE = "https://api.coinalyze.net/v1"
# Bybit's public tickers endpoint is globally accessible and returns the
# current funding rate for USDT-M perpetuals as a fraction per 8h. We prefer
# Bybit over Binance fapi because fapi.binance.com returns 451 from US-based
# cloud runtimes. Funding on Bybit vs Binance typically diverges by <2 bps
# in normal conditions — acceptable for positioning context.
BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/tickers"
SYMBOLS = list(CONFIG.coinalyze_symbols)
LIQUIDATION_WINDOW_HOURS = 72
LIQUIDATION_INTERVAL = "4hour"
BUCKETS_PER_24H = 6
CLUSTER_STDDEV_THRESHOLD = 2.0
TIMEOUT = 10.0


def _exchange_code(symbol: str) -> str:
    """BTCUSDT_PERP.A -> A;  BTCUSDT.6 -> 6;  BTCUSDT_PERP.3 -> 3"""
    return symbol.rsplit(".", 1)[-1]


def aggregate_open_interest(
    current_raw: list[dict], history_raw: list[dict], lookback_buckets: int = BUCKETS_PER_24H
) -> dict:
    """Sum current OI across venues. Compute 24h change using only venues
    with both current and historical data."""
    current_by_venue = {
        _exchange_code(r["symbol"]): (r.get("value") or 0.0)
        for r in current_raw
    }
    total_usd = sum(current_by_venue.values())

    history_by_venue = {}
    for r in history_raw:
        ex = _exchange_code(r["symbol"])
        hist = r.get("history") or []
        if len(hist) > lookback_buckets:
            # Compare current to value `lookback_buckets` bars ago
            c = hist[-(lookback_buckets + 1)].get("c")
            if c is not None:
                history_by_venue[ex] = c

    shared = sorted(set(current_by_venue) & set(history_by_venue))
    if shared:
        now_sum = sum(current_by_venue[v] for v in shared)
        then_sum = sum(history_by_venue[v] for v in shared)
        change_pct = (now_sum - then_sum) / then_sum * 100 if then_sum else 0.0
    else:
        change_pct = 0.0

    return {
        "total_usd": total_usd,
        "change_24h_pct": change_pct,
        "venues_used": shared if shared else sorted(current_by_venue.keys()),
    }



def aggregate_liquidations(liquidations_raw: list[dict], buckets_24h: int = BUCKETS_PER_24H) -> dict:
    """Sum long- and short-liquidation USD across venues over the last
    `buckets_24h` buckets."""
    long_total = 0.0
    short_total = 0.0
    for r in liquidations_raw:
        hist = r.get("history") or []
        for bucket in hist[-buckets_24h:]:
            long_total += bucket.get("l", 0.0) or 0.0
            short_total += bucket.get("s", 0.0) or 0.0
    if long_total == short_total == 0:
        side = "neutral"
    elif long_total > short_total:
        side = "long"
    else:
        side = "short"
    return {"long_usd": long_total, "short_usd": short_total, "dominant_side": side}


def detect_clusters(
    liquidations_raw: list[dict], stddev_threshold: float = CLUSTER_STDDEV_THRESHOLD
) -> list[dict]:
    """Flag buckets where total (long+short) liquidation USD, summed across
    venues for that bucket, exceeds mean + stddev_threshold * stddev of the
    full 72h window."""
    by_ts: dict[int, dict[str, float]] = {}
    for r in liquidations_raw:
        for bucket in r.get("history") or []:
            t = bucket["t"]
            by_ts.setdefault(t, {"l": 0.0, "s": 0.0})
            by_ts[t]["l"] += bucket.get("l", 0.0) or 0.0
            by_ts[t]["s"] += bucket.get("s", 0.0) or 0.0
    totals = {t: v["l"] + v["s"] for t, v in by_ts.items()}
    if len(totals) < 3:
        return []
    mean_total = statistics.mean(totals.values())
    sd = statistics.pstdev(totals.values())
    threshold = mean_total + stddev_threshold * sd
    clusters = []
    for t in sorted(by_ts.keys()):
        total = totals[t]
        if total > threshold:
            sums = by_ts[t]
            side = "long" if sums["l"] > sums["s"] else ("short" if sums["s"] > sums["l"] else "neutral")
            clusters.append({"t": t, "total_usd": total, "dominant_side": side})
    return clusters


def enrich_clusters_with_price(
    clusters: list[dict], bars_4h: list
) -> list[dict]:
    """Attach `price_high`, `price_low`, `price_close` to each cluster by
    matching its timestamp (Unix seconds) against the 4h OHLC bar whose
    open-time window contains it. Bars are OHLC objects (ts in ms).

    If no matching bar is found (cluster older than the fetched window),
    the price fields are set to None.
    """
    INTERVAL_MS = 4 * 3600 * 1000
    enriched = []
    for c in clusters:
        t_ms = int(c["t"]) * 1000
        match = None
        for b in bars_4h:
            if b.ts <= t_ms < b.ts + INTERVAL_MS:
                match = b
                break
        if match is None:
            enriched.append({**c, "price_high": None, "price_low": None, "price_close": None})
        else:
            enriched.append({
                **c,
                "price_high": match.high,
                "price_low": match.low,
                "price_close": match.close,
            })
    return enriched


def build_derivatives_payload(
    *,
    open_interest_raw: list[dict],
    open_interest_history_raw: list[dict],
    liquidations_raw: list[dict],
    funding: dict | None = None,
) -> dict:
    """Assemble the derivatives payload. Degrades per-section on partial
    upstream failure: fields from a missing source are set to None instead
    of faking a zero. status=unavailable only when every source is empty."""
    funding = funding or {"rate_8h_pct": None, "annualized_pct": None}
    has_oi = bool(open_interest_raw)
    has_liq = bool(liquidations_raw)
    has_funding = funding.get("annualized_pct") is not None

    if not (has_oi or has_liq or has_funding):
        return {"status": "unavailable", "error": "no data"}

    if has_oi:
        oi = aggregate_open_interest(open_interest_raw, open_interest_history_raw)
        oi_total = oi["total_usd"]
        oi_change = oi["change_24h_pct"]
        oi_venues = oi["venues_used"]
    else:
        oi_total = None
        oi_change = None
        oi_venues = []

    if has_liq:
        liq = aggregate_liquidations(liquidations_raw)
        clusters = detect_clusters(liquidations_raw)
    else:
        liq = None
        clusters = []

    missing = [
        name for name, present in (("oi", has_oi), ("liq", has_liq), ("funding", has_funding))
        if not present
    ]

    return {
        "status": "ok",
        "partial": bool(missing),
        "missing_sections": missing,
        "open_interest_usd": oi_total,
        "open_interest_change_24h_pct": oi_change,
        "funding_rate_8h_pct": funding["rate_8h_pct"],
        "funding_rate_annualized_pct": funding["annualized_pct"],
        "liquidations_24h": liq,
        "liquidation_clusters_72h": clusters,
        "venues_used": oi_venues,
    }


async def fetch_funding_rate(client: httpx.AsyncClient) -> dict:
    """Fetch funding rate for the active asset from Bybit's public tickers
    endpoint. Returns {rate_8h_pct, annualized_pct} or {None, None} on failure."""
    for attempt in range(2):
        try:
            r = await client.get(
                BYBIT_FUNDING_URL,
                params={"category": "linear", "symbol": CONFIG.symbol},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            ticker = data["result"]["list"][0]
            fr = float(ticker.get("fundingRate") or 0.0)
            return {
                "rate_8h_pct": fr * 100,
                "annualized_pct": fr * 3 * 365 * 100,
            }
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            if attempt == 1:
                return {"rate_8h_pct": None, "annualized_pct": None}
            await asyncio.sleep(2)
    return {"rate_8h_pct": None, "annualized_pct": None}


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> Any:
    api_key = os.environ.get("COINALYZE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("COINALYZE_API_KEY not set")
    for attempt in range(2):
        try:
            r = await client.get(
                f"{COINALYZE_BASE}{path}",
                params=params,
                headers={"api_key": api_key},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError):
            if attempt == 1:
                raise
            await asyncio.sleep(2)


async def fetch_all() -> dict:
    """Fetch Coinalyze OI/liquidation endpoints and Bybit funding in parallel.
    Uses return_exceptions so a single endpoint outage (e.g. Coinalyze 503
    on /open-interest only) does not discard the other sections. The builder
    then degrades per-section with explicit nulls."""
    now = int(time.time())
    from_ts = now - LIQUIDATION_WINDOW_HOURS * 3600
    syms = ",".join(SYMBOLS)

    async with httpx.AsyncClient() as client:
        oi, oi_hist, liq, funding = await asyncio.gather(
            _get(client, "/open-interest", {"symbols": syms, "convert_to_usd": "true"}),
            _get(client, "/open-interest-history", {
                "symbols": syms, "interval": LIQUIDATION_INTERVAL,
                "from": from_ts, "to": now, "convert_to_usd": "true",
            }),
            _get(client, "/liquidation-history", {
                "symbols": syms, "interval": LIQUIDATION_INTERVAL,
                "from": from_ts, "to": now, "convert_to_usd": "true",
            }),
            fetch_funding_rate(client),
            return_exceptions=True,
        )

    def _ok_list(v):
        return v if isinstance(v, list) else []

    def _ok_dict(v, default):
        return v if isinstance(v, dict) else default

    return build_derivatives_payload(
        open_interest_raw=_ok_list(oi),
        open_interest_history_raw=_ok_list(oi_hist),
        liquidations_raw=_ok_list(liq),
        funding=_ok_dict(funding, {"rate_8h_pct": None, "annualized_pct": None}),
    )
