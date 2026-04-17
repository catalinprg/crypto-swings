import asyncio
import httpx
from src.config import CONFIG
from src.types import OHLC, Timeframe

# data-api.binance.vision is Binance's public data mirror — same schema as
# api.binance.com/api/v3/klines but globally accessible (api.binance.com
# returns HTTP 451 from US-based cloud runtimes).
BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = CONFIG.symbol

TF_LOOKBACK: dict[Timeframe, int] = {
    "1M": 36,
    "1w": 104,
    "1d": 200,
    "4h": 300,
    "1h": 500,
}

def parse_klines(raw: list[list]) -> list[OHLC]:
    return [
        OHLC(
            ts=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
        for row in raw
    ]

async def fetch_one(client: httpx.AsyncClient, tf: Timeframe) -> list[OHLC]:
    for attempt in range(3):
        try:
            r = await client.get(
                BINANCE_URL,
                params={"symbol": SYMBOL, "interval": tf, "limit": TF_LOOKBACK[tf]},
                timeout=10.0,
            )
            r.raise_for_status()
            return parse_klines(r.json())
        except (httpx.HTTPError, httpx.TimeoutException, ValueError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** (attempt + 1))
    raise RuntimeError("unreachable")

async def fetch_all() -> dict[Timeframe, list[OHLC]]:
    async with httpx.AsyncClient() as client:
        tfs: list[Timeframe] = ["1M", "1w", "1d", "4h", "1h"]
        results = await asyncio.gather(*(fetch_one(client, tf) for tf in tfs))
        return dict(zip(tfs, results))
