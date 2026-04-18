"""Fair Value Gap detection (3-bar imbalance).

Bullish FVG: bars[i+1].low > bars[i-1].high → gap [bars[i-1].high, bars[i+1].low].
Bearish FVG: bars[i+1].high < bars[i-1].low → gap [bars[i+1].high, bars[i-1].low].

Lifecycle:
  - `mitigated`: any subsequent bar traded INTO the gap range → considered
    filled (pool spent).
  - `stale`: age (in bars) > stale_after and still unmitigated → still live
    but flagged — very old unfilled gaps lose magnet power over time.

We keep stale but unmitigated FVGs in the output (user preference) —
the `stale` flag lets the analyst de-weight them.

ATR filter: gaps smaller than MIN_GAP_ATR_MULT × atr_14 are suppressed —
crypto bars frequently produce tiny 1-tick imbalances that carry no structural
significance. The 0.05 multiplier is deliberately conservative; raise it to
0.1–0.2 on noisy assets.

Output from this module feeds Task 8 adapters that convert unmitigated FVGs
into Level objects for unified confluence clustering.
"""
from __future__ import annotations
from dataclasses import dataclass

from src.types import OHLC, Timeframe

# Minimum gap size expressed as a fraction of ATR(14).
# Gaps smaller than this are suppressed as structural noise.
MIN_GAP_ATR_MULT: float = 0.05

# Default age threshold (bars) beyond which an unmitigated FVG is flagged stale.
DEFAULT_STALE_AFTER: int = 100


@dataclass(frozen=True)
class FVG:
    type: str              # "FVG_BULL" | "FVG_BEAR"
    tf: Timeframe
    lo: float
    hi: float
    formation_ts: int
    age_bars: int
    mitigated: bool
    stale: bool


def detect_fvgs(
    bars: list[OHLC],
    *,
    tf: Timeframe,
    atr_14: float,
    stale_after: int = DEFAULT_STALE_AFTER,
) -> list[FVG]:
    """Scan 3-bar windows; report every FVG formed in the window, with
    mitigation and stale flags computed against all subsequent bars."""
    if len(bars) < 3:
        return []

    min_gap = MIN_GAP_ATR_MULT * atr_14
    out: list[FVG] = []
    n = len(bars)

    for i in range(1, n - 1):
        prev, mid, nxt = bars[i - 1], bars[i], bars[i + 1]

        # Bullish FVG
        if nxt.low > prev.high:
            gap_lo, gap_hi = prev.high, nxt.low
            if gap_hi - gap_lo >= min_gap:
                mit = _is_mitigated(bars, i + 1, gap_lo, gap_hi)
                age = n - 1 - (i + 1)
                out.append(FVG(
                    type="FVG_BULL", tf=tf, lo=gap_lo, hi=gap_hi,
                    formation_ts=mid.ts, age_bars=age,
                    mitigated=mit, stale=(age > stale_after and not mit),
                ))

        # Bearish FVG
        if nxt.high < prev.low:
            gap_lo, gap_hi = nxt.high, prev.low
            if gap_hi - gap_lo >= min_gap:
                mit = _is_mitigated(bars, i + 1, gap_lo, gap_hi)
                age = n - 1 - (i + 1)
                out.append(FVG(
                    type="FVG_BEAR", tf=tf, lo=gap_lo, hi=gap_hi,
                    formation_ts=mid.ts, age_bars=age,
                    mitigated=mit, stale=(age > stale_after and not mit),
                ))

    return out


def _is_mitigated(bars: list[OHLC], start_idx: int, lo: float, hi: float) -> bool:
    for b in bars[start_idx + 1:]:
        if b.low <= hi and b.high >= lo:
            return True
    return False
