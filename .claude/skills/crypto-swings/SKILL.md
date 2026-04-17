---
name: crypto-swings
description: Full crypto swings analysis pipeline for a single asset (BTC or ETH). Fetches OHLC from Binance across 5 timeframes + derivatives (OI, funding, liquidations) from Coinalyze/Bybit, computes Fibonacci confluence zones, dispatches the crypto-swings-analyst agent to produce a hedged Romanian briefing, publishes to Notion under the asset's Swings parent, notifies Telegram. Takes an `asset` argument (`btc` or `eth`). Use when the user wants BTC or ETH S/R analysis, swing levels, or a trading briefing.
---

You are executing the crypto-swings analysis pipeline for a single asset.

## Arguments

- `asset` — one of `btc` or `eth`. Required. The entire pipeline runs for this asset only; to brief both, run the skill twice.

If the argument is missing or not one of `btc` / `eth`, stop and ask the user which asset to run.

## Step 1 — Refresh repo and emit payload

Run these from the repo root in order. Export `ASSET` so every downstream call sees it:

```bash
export ASSET=<asset>         # btc or eth
git checkout main
git pull --ff-only
python3 -m scripts.emit_payload data/payload.json
```

`git checkout main` ensures HEAD is on the canonical branch — resumed cloud environments may still be on a prior `claude/...` branch from an earlier run, which would make `git pull --ff-only` fail. `git pull --ff-only` then picks up any code changes since the session started.

`emit_payload.py` reads `ASSET` and loads `config/$ASSET.json`, fetches Binance OHLC across 5 timeframes, pulls derivatives from Coinalyze + Bybit (OI, funding rate, 72h liquidation history with price-at-time enrichment), computes Fibonacci confluence zones with ATR-adaptive clustering, and writes `data/payload.json`.

Required env vars (set on the cloud environment, not committed):
- `ASSET` — `btc` or `eth`. Selects which config file is loaded.
- `COINALYZE_API_KEY` — for OI and liquidation data. If unset, the derivatives section degrades to `status=unavailable` and the pipeline continues with pure fib analysis.

Wait for the command to complete. If it exits with a non-zero code or logs a fatal error, stop and report the failure. Do not proceed with stale or missing data.

The last line of stdout confirms what was emitted, e.g.:
```
payload written: data/payload.json
current: 74845.05 resistance: 8 support: 8 derivatives: ok
```

## Step 2 — Capture timestamp

Run:

```bash
echo $(date +%Y%m%d_%H%M%S)
```

Use that value as TIMESTAMP (e.g. `20260417_064530`). This will be the Notion page title.

## Step 3 — Dispatch crypto-swings-analyst agent

Use the Agent tool to spawn the `crypto-swings-analyst` agent with this minimal prompt:

```
Read and analyze: data/payload.json

Write your complete briefing as Markdown to data/briefing.md using the Write tool. Do not include a top-level page title — the publisher sets it. After the file is saved, respond with exactly: done data/briefing.md
```

That is the complete prompt. Do not add more context — the agent has its full instructions (role, language rules, analysis framework, output format) embedded, and reads the asset identity from the payload's `asset` / `display_name` fields.

## Step 4 — Publish to Notion

Once the agent returns `done data/briefing.md`, run:

```bash
python3 publish_notion.py data/briefing.md TIMESTAMP
```

Substitute the actual TIMESTAMP from Step 2. `publish_notion.py` reads `ASSET` and routes the page under the correct parent (`BTC Swings` or `ETH Swings`). The script prints the new Notion page URL on its last stdout line.

Required env var: `NOTION_TOKEN` (Notion Internal Integration Token). The parent page for the active asset must be shared with the integration.

If the script exits with a non-zero code, capture stderr and report the failure to the user.

## Step 5 — Notify Telegram (non-fatal)

After a successful publish, fire the Telegram notification:

```bash
python3 notify_telegram.py "$(echo $ASSET | tr a-z A-Z) Swings briefing published $(date +%Y-%m-%d\ %H:%M)
[View on Notion](<notion_url>)"
```

Substitute `<notion_url>` with the URL printed by `publish_notion.py`.

Required env vars (per-asset values set by the Routines trigger):
- `TELEGRAM_BOT_TOKEN` — bot token from @BotFather
- `TELEGRAM_CHAT_ID`   — asset-specific chat ID

The script is idempotent: if either env var is unset it exits 0 silently. If the API call fails, it exits non-zero — treat that as non-fatal and continue to Step 6. Do not fail the whole pipeline on a notification error.

## Step 6 — Confirm

Report to the user:
- **On success:** `Analysis uploaded to Notion: <notion_url>`
- **On agent failure:** `$ASSET Swings failed at analysis step: <error from agent>`
- **On publish failure:** `$ASSET Swings failed at publish step: <stderr>`

If Step 5 failed (Telegram API error), append a second line: `Telegram notification failed: <stderr>`.

Do not include multiple outcomes. Do not wrap the URL in extra commentary.
