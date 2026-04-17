"""Telegram notifier for the active asset's Swings briefing.

Env vars (required at send time, not at import):
  TELEGRAM_BOT_TOKEN  Bot token from @BotFather
  TELEGRAM_CHAT_ID    Target chat ID (numeric). Per-asset: each Routines
                      trigger sets its own value for BTC or ETH.

If either env var is unset, send() exits silently (no-op).
On API error, raises RuntimeError — caller logs and continues.
"""
import os

import httpx

from src.config import CONFIG
from src.types import Zone

API_BASE = "https://api.telegram.org"
TIMEOUT = 10


def build_summary(
    *,
    current_price: float,
    top_support: list[Zone],
    top_resistance: list[Zone],
    notion_url: str,
    max_each_side: int = 2,
) -> str:
    lines = [f"*{CONFIG.display_name} Swings* — ${current_price:,.0f}", ""]
    if top_resistance:
        lines.append("*Resistance:*")
        for z in top_resistance[:max_each_side]:
            lines.append(f"  · ${z.mid:,.0f} (score {z.score})")
    if top_support:
        lines.append("*Support:*")
        for z in top_support[:max_each_side]:
            lines.append(f"  · ${z.mid:,.0f} (score {z.score})")
    lines.append("")
    lines.append(notion_url)
    return "\n".join(lines)


async def send(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("telegram not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID unset); skipping")
        return

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=TIMEOUT,
        )

    if r.status_code >= 400:
        raise RuntimeError(f"telegram sendMessage failed {r.status_code}: {r.text}")

    print("telegram notification sent")
