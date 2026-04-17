"""Per-asset configuration loader.

Reads `config/{asset}.json` based on the ASSET env var (default: "btc").
Exposes a module-level `CONFIG` singleton that other modules import. Keeping
configuration in JSON files (rather than Python) lets a new asset be added
without touching code.

The default of "btc" when ASSET is unset preserves local-test and
legacy-invocation behavior from the btc-swings repo this module was merged
from. Production runs (Routines triggers) must set ASSET explicitly.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_ASSETS = ("btc", "eth")
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass(frozen=True)
class AssetConfig:
    asset: str
    display_name: str
    symbol: str
    coinalyze_symbols: tuple[str, ...]
    notion_parent_id: str


def load_config(asset: str) -> AssetConfig:
    asset = asset.lower().strip()
    if asset not in SUPPORTED_ASSETS:
        raise ValueError(
            f"unsupported ASSET={asset!r}; expected one of {SUPPORTED_ASSETS}"
        )
    path = CONFIG_DIR / f"{asset}.json"
    if not path.exists():
        raise FileNotFoundError(f"config file missing: {path}")
    raw = json.loads(path.read_text())
    return AssetConfig(
        asset=raw["asset"],
        display_name=raw["display_name"],
        symbol=raw["symbol"],
        coinalyze_symbols=tuple(raw["coinalyze_symbols"]),
        notion_parent_id=raw["notion_parent_id"],
    )


CONFIG: AssetConfig = load_config(os.environ.get("ASSET", "btc"))
