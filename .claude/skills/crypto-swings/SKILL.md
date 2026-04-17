---
name: crypto-swings
description: Full crypto swings analysis pipeline. Fetches OHLC from Binance across 5 timeframes + derivatives (OI, funding, liquidations) from Coinalyze/Bybit, computes Fibonacci confluence zones, dispatches the crypto-swings-analyst agent to produce a hedged Romanian briefing, publishes to Notion under the asset's Swings parent, notifies Telegram. Takes an `asset` argument — `btc`, `eth`, or `all` (runs both sequentially in one session to conserve Routines quota). Use when the user wants BTC or ETH S/R analysis, swing levels, or a trading briefing.
---

You are executing the crypto-swings analysis pipeline.

## Arguments

- `asset` — one of `btc`, `eth`, or `all`. Required.
  - `btc` / `eth` — run the pipeline for that asset only.
  - `all` — run the full pipeline for BTC, then for ETH, sequentially, in a single session. Used by the consolidated Routines trigger to produce both briefings in one scheduled run (halves the Routines quota cost).

If the argument is missing or not one of `btc` / `eth` / `all`, stop and ask the user which asset to run.

## Dispatch

- **`asset == btc` or `asset == eth`:** execute Steps 1–6 once for that asset.
- **`asset == all`:** execute Steps 1–6 first for `btc`, then for `eth`. Each asset runs as an **independent unit with its own error handling** — a failure in BTC must NOT skip ETH. Collect per-asset outcomes and report both at the end (see "Step 6 — Confirm" below for the `all` reporting format).

## Step 1 — Refresh repo and emit payload

Run these from the repo root in order. Export `ASSET` so every downstream call sees it. In `all` mode, re-export `ASSET` before each asset's run:

```bash
export ASSET=<asset>         # btc or eth (NOT "all" — always a specific asset here)
git checkout main
git pull --ff-only
python3 -m scripts.emit_payload data/payload.json
```

(In `all` mode, `git checkout main` / `git pull` only needs to run once — before the first asset. Just re-export `ASSET` between assets.)

`git checkout main` ensures HEAD is on the canonical branch — resumed cloud environments may still be on a prior `claude/...` branch from an earlier run, which would make `git pull --ff-only` fail. `git pull --ff-only` then picks up any code changes since the session started.

`emit_payload.py` reads `ASSET` and loads `config/$ASSET.json`, fetches Binance OHLC across 5 timeframes, pulls derivatives from Coinalyze + Bybit (OI, funding rate, 72h liquidation history with price-at-time enrichment), computes Fibonacci confluence zones with ATR-adaptive clustering, and writes `data/payload.json`.

Required env vars (set on the cloud environment, not committed):
- `ASSET` — `btc` or `eth`. Selects which config file is loaded.
- `COINALYZE_API_KEY` — for OI and liquidation data. If unset, the derivatives section degrades to `status=unavailable` and the pipeline continues with pure fib analysis.

Wait for the command to complete. If it exits with a non-zero code or logs a fatal error:
- In single-asset mode: stop and report the failure.
- In `all` mode: record the failure for this asset (see Step 6) and **continue with the next asset** (re-start Step 1 for the next asset). Do not abort the whole session on one asset's failure.

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

Use that value as TIMESTAMP (e.g. `20260417_064530`). This will be the Notion page title. In `all` mode, capture a fresh TIMESTAMP for each asset.

## Step 3 — Dispatch crypto-swings-analyst agent

Use the Agent tool to spawn the `crypto-swings-analyst` agent with this minimal prompt:

```
Read and analyze: data/payload.json

Write your complete briefing as Markdown to data/briefing.md using the Write tool. Do not include a top-level page title — the publisher sets it. After the file is saved, respond with exactly: done data/briefing.md
```

That is the complete prompt. Do not add more context — the agent has its full instructions (role, language rules, analysis framework, output format) embedded, and reads the asset identity from the payload's `asset` / `display_name` fields.

In `all` mode, the agent overwrites `data/briefing.md` each time — this is expected. Step 4 must run immediately after the agent returns and before the next asset overwrites the file.

## Step 4 — Publish to Notion

Once the agent returns `done data/briefing.md`, run:

```bash
python3 publish_notion.py data/briefing.md TIMESTAMP
```

Substitute the actual TIMESTAMP from Step 2. `publish_notion.py` reads `ASSET` and routes the page under the correct parent (`BTC Swings` or `ETH Swings`). The script prints the new Notion page URL on its last stdout line.

Required env var: `NOTION_TOKEN` (Notion Internal Integration Token). The parent page for the active asset must be shared with the integration.

If the script exits with a non-zero code, capture stderr. In single-asset mode report and stop. In `all` mode record the failure for this asset and continue with the next asset.

## Step 5 — Notify Telegram (non-fatal)

Fire the notification. `notify_telegram.py` reads `ASSET` and picks the right chat ID automatically — no shell-env juggling required, which would not survive across separate Bash tool calls in the cloud runtime anyway:

```bash
python3 notify_telegram.py "$(echo $ASSET | tr a-z A-Z) Swings briefing published $(date +%Y-%m-%d\ %H:%M)
[View on Notion](<notion_url>)"
```

Substitute `<notion_url>` with the URL printed by `publish_notion.py`. Make sure `ASSET` is still exported in this Bash call; if not, prefix inline: `ASSET=<asset> python3 notify_telegram.py "..."`.

Required env vars:
- `TELEGRAM_BOT_TOKEN` — shared across assets (one bot).
- `TELEGRAM_CHAT_ID_BTC` and/or `TELEGRAM_CHAT_ID_ETH` — per-asset chat IDs. The script resolves the right one from `ASSET`.
- Legacy fallback: `TELEGRAM_CHAT_ID` — used only if the per-asset var is missing. Good for one-off single-asset runs.

The script is idempotent: if the resolved chat ID or bot token is unset, it exits 0 silently with a diagnostic line telling you which env vars it looked at. If the API call fails, it exits non-zero — treat that as non-fatal. Do not fail the whole pipeline on a notification error.

## Step 6 — Confirm

**Single-asset mode** — report one outcome to the user:
- **On success:** `Analysis uploaded to Notion: <notion_url>`
- **On agent failure:** `$ASSET Swings failed at analysis step: <error from agent>`
- **On publish failure:** `$ASSET Swings failed at publish step: <stderr>`
- If Step 5 failed (Telegram API error), append a second line: `Telegram notification failed: <stderr>`.

Do not include multiple outcomes. Do not wrap the URL in extra commentary.

**`all` mode** — after both assets have run (or attempted to run), report one consolidated message with one line per asset:

```
Crypto Swings (all):
- BTC: <notion_url>   ← or: BTC: failed at <step> — <error summary>
- ETH: <notion_url>   ← or: ETH: failed at <step> — <error summary>
```

If any asset's Telegram failed non-fatally, append a trailing line: `Telegram notification failed for: BTC` (or `ETH`, or `BTC, ETH`).

Do not return early just because one asset failed — always report the final state of both.
