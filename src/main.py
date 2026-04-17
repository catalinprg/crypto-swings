import asyncio
import sys
from src.config import CONFIG
from src.confluence import cluster, split_by_price
from src import derivatives as derivatives_mod
from src.fetch import fetch_all
from src.fibs import compute_all
from src.notion_writer import NOTION_PARENT_ID, build_page_payload
from src.swings import atr, detect_swings
from src.telegram_notify import build_summary, send as send_telegram
from src.types import Timeframe, Zone

MIN_PAIRS_PER_TF = 2
MIN_TFS_REQUIRED = 2
ATR_CLUSTER_MULTIPLIER = 0.25
MAX_EXTENSION_DISTANCE_PCT = 0.15

async def run() -> int:
    # 1. Fetch OHLC + derivatives in parallel
    try:
        ohlc, deriv = await asyncio.gather(
            fetch_all(),
            derivatives_mod.fetch_all(),
        )
    except Exception as e:
        await _notify_failure(f"{CONFIG.display_name} Swings: data fetch failed — {e}")
        return 1

    # 2-3. Swings + fibs per TF
    all_pairs = []
    contributing: list[Timeframe] = []
    skipped: list[Timeframe] = []
    for tf, bars in ohlc.items():
        pairs = detect_swings(bars, tf=tf, max_pairs=3)
        if len(pairs) < MIN_PAIRS_PER_TF:
            skipped.append(tf)
            continue
        all_pairs.extend(pairs)
        contributing.append(tf)

    if len(contributing) < MIN_TFS_REQUIRED:
        await _notify_failure(
            f"{CONFIG.display_name} Swings: insufficient swing data "
            f"(only {len(contributing)} TFs viable)"
        )
        return 1

    levels = compute_all(all_pairs)

    # 4. Cluster using daily ATR as the radius base
    daily_bars = ohlc["1d"]
    current_price = daily_bars[-1].close

    # Drop extension levels that project more than 15% from current price —
    # they're mathematically valid but not actionable S/R at today's horizon.
    levels = [
        l for l in levels
        if l.kind == "retracement"
        or abs(l.price - current_price) / current_price <= MAX_EXTENSION_DISTANCE_PCT
    ]

    daily_atr = _latest(atr(daily_bars, 14))
    radius = daily_atr * ATR_CLUSTER_MULTIPLIER
    zones = cluster(levels, radius=radius)

    # 5. Split and rank
    prev_close = daily_bars[-2].close if len(daily_bars) >= 2 else current_price
    change_24h_pct = (current_price - prev_close) / prev_close * 100
    support, resistance = split_by_price(zones, current_price)

    # 6. Notion page
    payload = build_page_payload(
        current_price=current_price,
        change_24h_pct=change_24h_pct,
        atr_daily=daily_atr,
        support=support,
        resistance=resistance,
        contributing_tfs=contributing,
        skipped_tfs=skipped,
        parent_page_id=NOTION_PARENT_ID,
    )
    if deriv.get("status") == "ok" and deriv.get("liquidation_clusters_72h"):
        deriv["liquidation_clusters_72h"] = derivatives_mod.enrich_clusters_with_price(
            deriv["liquidation_clusters_72h"], ohlc["4h"]
        )
    payload["derivatives"] = deriv

    notion_url = await _write_notion_with_retry(payload)

    # 7. Telegram
    try:
        if notion_url:
            summary = build_summary(
                current_price=current_price,
                top_support=support,
                top_resistance=resistance,
                notion_url=notion_url,
            )
        else:
            # Fallback: carry full briefing in message body
            summary = payload["body"][:4000]
        await send_telegram(summary)
    except Exception as e:
        print(f"telegram failed (non-fatal): {e}", file=sys.stderr)

    return 0

def _latest(series: list[float | None]) -> float:
    for v in reversed(series):
        if v is not None:
            return v
    raise RuntimeError("no ATR value available")

async def _write_notion_with_retry(payload: dict) -> str | None:
    for attempt in range(2):
        try:
            return await write_to_notion(payload)
        except Exception as e:
            if attempt == 1:
                print(f"notion write failed: {e}", file=sys.stderr)
                return None
            await asyncio.sleep(3)
    return None

async def write_to_notion(payload: dict) -> str:
    """Call the Notion MCP tool `notion-create-pages` with the payload.

    In the cloud Claude Code session this function is replaced at runtime:
    the session operator executes the MCP call using `payload['parent_page_id']`,
    `payload['title']`, and `payload['body']`, then returns the created page URL.

    For local/test runs, monkeypatch this function.
    """
    raise NotImplementedError(
        "write_to_notion must be provided by the cloud session MCP harness"
    )

async def _notify_failure(message: str) -> None:
    try:
        await send_telegram(message)
    except Exception as e:
        print(f"{message}\n(telegram also failed: {e})", file=sys.stderr)

if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
