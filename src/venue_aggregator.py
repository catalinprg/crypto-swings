"""Cross-venue OHLCV aggregation for volume-profile and AVWAP.

Binance remains the primary (reference) venue: OHLC per timestamp is taken
from Binance when present (stable reference for price levels). Volume is
SUMMED across Binance + Bybit + Coinbase — that's the whole point of
aggregation.

For swing/fib computation we continue to use Binance-only (keeps historic
comparability and is already well-tested). Aggregation is specifically for
VP + AVWAP where volume fidelity dominates.

Fetch endpoints (all public, no auth, US-cloud accessible):
  - Binance spot:  data-api.binance.vision/api/v3/klines
  - Bybit spot:    api.bybit.com/v5/market/kline?category=spot
  - Coinbase:      api.exchange.coinbase.com/products/{product}/candles

Coinbase supports granularities {60, 300, 900, 3600, 21600, 86400}s —
1h + 1d native. 4h/1w/1M get resampled from 1h (4h) or 1d (1w/1M).
"""
from __future__ import annotations
import asyncio
import httpx
from collections import defaultdict
from typing import Iterable

from src.types import OHLC, Timeframe

BINANCE_URL  = "https://data-api.binance.vision/api/v3/klines"
BYBIT_URL    = "https://api.bybit.com/v5/market/kline"
COINBASE_URL = "https://api.exchange.coinbase.com/products/{product}/candles"

# Bybit interval codes
BYBIT_INTERVAL: dict[Timeframe, str] = {
    "1M": "M", "1w": "W", "1d": "D", "4h": "240", "1h": "60",
}

# Coinbase supports only native granularities. 4h / 1w / 1M resampled.
COINBASE_GRANULARITY: dict[Timeframe, int | None] = {
    "1M": None,    # resample from 1d
    "1w": None,    # resample from 1d
    "1d": 86400,
    "4h": None,    # resample from 1h
    "1h": 3600,
}


def _coinbase_product(symbol: str) -> str:
    """BTCUSDT -> BTC-USD, ETHUSDT -> ETH-USD.
    Coinbase trades vs USD, not USDT. Quote-currency drift accepted as <1 bin
    noise given ATR-relative bin width used in VP."""
    base = symbol.replace("USDT", "")
    return f"{base}-USD"


def aggregate_bars(
    by_venue: dict[str, list[OHLC]],
    primary: str = "binance",
) -> list[OHLC]:
    """Merge per-venue bars by timestamp. Volume SUMMED. OHLC taken from
    `primary` when present, otherwise from the first venue that has a bar
    at that timestamp (stable preference order)."""
    buckets: dict[int, dict[str, OHLC]] = defaultdict(dict)
    for venue, bars in by_venue.items():
        for b in bars:
            buckets[b.ts][venue] = b
    preference_order = [primary] + [v for v in by_venue if v != primary]
    out: list[OHLC] = []
    for ts in sorted(buckets):
        venue_bars = buckets[ts]
        ref: OHLC | None = None
        for v in preference_order:
            if v in venue_bars:
                ref = venue_bars[v]
                break
        if ref is None:
            continue
        total_vol = sum(b.volume for b in venue_bars.values())
        out.append(OHLC(
            ts=ts, open=ref.open, high=ref.high, low=ref.low,
            close=ref.close, volume=total_vol,
            taker_buy_volume=ref.taker_buy_volume,
        ))
    return out


# ---- Fetch adapters ----

async def fetch_bybit(
    client: httpx.AsyncClient, symbol: str, tf: Timeframe, limit: int
) -> list[OHLC]:
    """Bybit V5 spot kline. Returns [] on error."""
    try:
        r = await client.get(
            BYBIT_URL,
            params={
                "category": "spot", "symbol": symbol,
                "interval": BYBIT_INTERVAL[tf], "limit": str(limit),
            },
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("result", {}).get("list", [])
        # Bybit returns newest-first; reverse for chronological
        out: list[OHLC] = []
        for row in reversed(rows):
            out.append(OHLC(
                ts=int(row[0]),
                open=float(row[1]), high=float(row[2]),
                low=float(row[3]),  close=float(row[4]),
                volume=float(row[5]),
            ))
        return out
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return []


async def fetch_coinbase_native(
    client: httpx.AsyncClient, product: str, granularity: int, limit: int = 300
) -> list[OHLC]:
    """Coinbase exchange candles. Returns [[ts, low, high, open, close, vol], ...].
    Response is newest-first. Limit capped at 300 per request."""
    try:
        r = await client.get(
            COINBASE_URL.format(product=product),
            params={"granularity": str(granularity)},
            timeout=10.0,
        )
        r.raise_for_status()
        rows = r.json()
        out: list[OHLC] = []
        for row in reversed(rows):
            # timestamp is in seconds → convert to ms for consistency
            out.append(OHLC(
                ts=int(row[0]) * 1000,
                open=float(row[3]), high=float(row[2]),
                low=float(row[1]),  close=float(row[4]),
                volume=float(row[5]),
            ))
        return out
    except (httpx.HTTPError, ValueError, IndexError):
        return []


def resample(bars: list[OHLC], tf_from: Timeframe, tf_to: Timeframe) -> list[OHLC]:
    """Group `tf_from` bars into `tf_to` bars by timestamp floor. OHLC rebuilt
    from first/max/min/last; volume summed. Used for Coinbase 4h (from 1h),
    1w (from 1d), 1M (from 1d)."""
    if not bars:
        return []
    bucket_ms = _bucket_ms(tf_to)
    buckets: dict[int, list[OHLC]] = defaultdict(list)
    for b in bars:
        key = (b.ts // bucket_ms) * bucket_ms
        buckets[key].append(b)
    out: list[OHLC] = []
    for ts in sorted(buckets):
        grp = buckets[ts]
        out.append(OHLC(
            ts=ts,
            open=grp[0].open,
            high=max(b.high for b in grp),
            low=min(b.low for b in grp),
            close=grp[-1].close,
            volume=sum(b.volume for b in grp),
        ))
    return out


def _bucket_ms(tf: Timeframe) -> int:
    return {
        "1h":  3_600_000,
        "4h":  14_400_000,
        "1d":  86_400_000,
        "1w":  7 * 86_400_000,
        "1M":  30 * 86_400_000,   # nominal; OK for bucketing into 30d windows
    }[tf]


async def fetch_coinbase(
    client: httpx.AsyncClient, symbol: str, tf: Timeframe
) -> list[OHLC]:
    product = _coinbase_product(symbol)
    g = COINBASE_GRANULARITY[tf]
    if g is not None:
        return await fetch_coinbase_native(client, product, g)
    # Resampled path: 4h from 1h; 1w/1M from 1d
    if tf == "4h":
        src = await fetch_coinbase_native(client, product, 3600)
        return resample(src, "1h", "4h")
    # 1w or 1M → from 1d
    src = await fetch_coinbase_native(client, product, 86400)
    return resample(src, "1d", tf)


async def fetch_all_venues(
    symbol: str, tf: Timeframe, limit: int,
) -> dict[str, list[OHLC]]:
    """Fetch one TF across Binance, Bybit, Coinbase in parallel. Binance is
    fetched by the caller via src.fetch — we do not re-fetch it here; the
    caller passes Binance bars in.

    This function returns Bybit + Coinbase bars only; merge with Binance at
    the call site using `aggregate_bars`."""
    async with httpx.AsyncClient() as client:
        bybit, coinbase = await asyncio.gather(
            fetch_bybit(client, symbol, tf, limit),
            fetch_coinbase(client, symbol, tf),
            return_exceptions=True,
        )
    return {
        "bybit": bybit if isinstance(bybit, list) else [],
        "coinbase": coinbase if isinstance(coinbase, list) else [],
    }
