"""ICT Order Blocks with 1.5×ATR displacement filter.

Bullish OB = the last down candle (close < open) that precedes a
displacement UP candle whose range exceeds 1.5×ATR and whose close breaks
above the most recent swing high within the lookback window.

Bearish OB = mirror: the last up candle (close > open) before a
displacement DOWN candle whose range exceeds 1.5×ATR and whose close
breaks below the most recent swing low within the lookback window.

Lifecycle:
  - `mitigated`: subsequent bar trades into the OB's price range → spent.
  - `stale`: age (in bars) > stale_after AND unmitigated → flagged but
    retained (de-weight rather than discard).

OB range = the precursor candle's [low, high].

NOTE: age_bars is measured from the OB candle (ob_idx), not from the
displacement bar. This differs from fvg.py, which measures from i+1 (the
completing bar of the 3-bar window). Task 8 consumers should be aware
when comparing ages across sources.

Output from this module feeds Task 8 adapters that convert unmitigated
Order Blocks into Level objects for unified confluence clustering.
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

# Displacement must be at least this multiple of ATR(14) to qualify.
DISPLACEMENT_ATR_MULT: float = 1.5

# Number of bars looked back to find the prior swing high/low.
# NOTE: Fixed across TFs. On 1w/1M this spans months; consider TF-scaled
# lookback in Task 9 when wiring per-TF config.
PRIOR_SWING_LOOKBACK: int = 20

# Default age threshold (bars) beyond which an unmitigated OB is flagged stale.
DEFAULT_STALE_AFTER: int = 100


@dataclass(frozen=True)
class OrderBlock:
    type: str            # "OB_BULL" | "OB_BEAR"
    tf: Timeframe
    lo: float
    hi: float
    formation_ts: int
    age_bars: int
    mitigated: bool
    stale: bool


def detect_order_blocks(
    bars: list[OHLC],
    *,
    tf: Timeframe,
    atr_14: float,
    stale_after: int = DEFAULT_STALE_AFTER,
) -> list[OrderBlock]:
    """Scan bars for ICT Order Block setups.

    Each qualifying displacement bar is checked for:
      1. Range >= DISPLACEMENT_ATR_MULT × atr_14.
      2. Close breaks the most recent swing high (bullish) or low (bearish)
         within PRIOR_SWING_LOOKBACK bars.

    The OB itself is the last opposing candle immediately before the
    displacement bar.

    Returns a deduplicated list keyed on (type, formation_ts); the most
    recently processed record wins (post-mitigation state takes priority).
    """
    if len(bars) < 3 or atr_14 <= 0:
        return []

    threshold = DISPLACEMENT_ATR_MULT * atr_14
    out: list[OrderBlock] = []
    n = len(bars)

    for i in range(1, n):
        disp = bars[i]
        disp_range = disp.high - disp.low
        if disp_range < threshold:
            continue

        # --- Bullish displacement ---
        if disp.close > disp.open:
            look_lo = max(0, i - PRIOR_SWING_LOOKBACK)
            prior_high = max(b.high for b in bars[look_lo:i])
            if disp.close <= prior_high:
                continue
            ob_idx = _last_down_before(bars, i)
            if ob_idx is None:
                continue
            ob = bars[ob_idx]
            mit = _mitigated(bars, ob_idx, ob.low, ob.high)
            age = n - 1 - ob_idx
            out.append(OrderBlock(
                type="OB_BULL", tf=tf, lo=ob.low, hi=ob.high,
                formation_ts=ob.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))

        # --- Bearish displacement ---
        elif disp.close < disp.open:
            look_lo = max(0, i - PRIOR_SWING_LOOKBACK)
            prior_low = min(b.low for b in bars[look_lo:i])
            if disp.close >= prior_low:
                continue
            ob_idx = _last_up_before(bars, i)
            if ob_idx is None:
                continue
            ob = bars[ob_idx]
            mit = _mitigated(bars, ob_idx, ob.low, ob.high)
            age = n - 1 - ob_idx
            out.append(OrderBlock(
                type="OB_BEAR", tf=tf, lo=ob.low, hi=ob.high,
                formation_ts=ob.ts, age_bars=age,
                mitigated=mit, stale=(age > stale_after and not mit),
            ))

    # Deduplicate: keep the last (most complete) record per (type, formation_ts).
    seen: dict[tuple[str, int], OrderBlock] = {}
    for o in out:
        seen[(o.type, o.formation_ts)] = o
    return list(seen.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _last_down_before(bars: list[OHLC], end_idx: int) -> int | None:
    """Return the index of the last bar with close < open before end_idx."""
    for k in range(end_idx - 1, -1, -1):
        if bars[k].close < bars[k].open:
            return k
    return None


def _last_up_before(bars: list[OHLC], end_idx: int) -> int | None:
    """Return the index of the last bar with close > open before end_idx."""
    for k in range(end_idx - 1, -1, -1):
        if bars[k].close > bars[k].open:
            return k
    return None


def _mitigated(bars: list[OHLC], from_idx: int, lo: float, hi: float) -> bool:
    """Return True if any bar after the displacement bar trades into [lo, hi].

    Index layout:
      from_idx     = OB candle
      from_idx + 1 = displacement candle (creates the OB — not a mitigation)
      from_idx + 2 = first bar eligible to mitigate
    """
    for b in bars[from_idx + 2:]:
        if b.low <= hi and b.high >= lo:
            return True
    return False
