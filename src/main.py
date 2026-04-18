import asyncio
import sys
from src.config import CONFIG
from src import derivatives as derivatives_mod  # noqa: F401  (patched by tests via main_mod.derivatives_mod)
from src.notion_writer import NOTION_PARENT_ID, build_page_payload
from src.telegram_notify import build_summary, send as send_telegram

MIN_PAIRS_PER_TF = 2
ATR_CLUSTER_MULTIPLIER = 0.25
MAX_EXTENSION_DISTANCE_PCT = 0.15


async def run() -> int:
    try:
        from scripts.emit_payload import build as build_payload
        payload = await build_payload()
    except Exception as e:
        await _notify_failure(f"{CONFIG.display_name} Swings: payload build failed — {e}")
        return 1

    notion_page = build_page_payload(
        current_price=payload["current_price"],
        change_24h_pct=payload["change_24h_pct"],
        atr_daily=payload["daily_atr"],
        support=payload["support"],
        resistance=payload["resistance"],
        contributing_tfs=payload["contributing_tfs"],
        skipped_tfs=payload["skipped_tfs"],
        parent_page_id=NOTION_PARENT_ID,
    )
    notion_page["derivatives"] = payload["derivatives"]

    notion_url = await _write_notion_with_retry(notion_page)

    try:
        if notion_url:
            summary = build_summary(
                current_price=payload["current_price"],
                top_support=payload["support"],
                top_resistance=payload["resistance"],
                notion_url=notion_url,
            )
        else:
            summary = notion_page["body"][:4000]
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
