"""Market structure analysis — BOS (Break of Structure) + CHoCH (Change of
Character) derived from the same swing pivots swings.py already produces.

Bias definition:
  bullish: sequence of higher-highs (HH) AND higher-lows (HL)
  bearish: sequence of lower-highs (LH) AND lower-lows (LL)
  range:   neither clean pattern

BOS: continuation. The last confirmed pivot in the trend direction (the most
recent HH in bullish / most recent LL in bearish). Confirmed historically
by the pivot sequence; does NOT require current_price to have exceeded it.

CHoCH: reversal. Current price has already crossed the most recent
counter-trend pivot (HL in bullish / LH in bearish), signalling a live
change of character.

Output feeds Task 8 adapters → Level objects (MS_BOS_LEVEL, MS_CHOCH_LEVEL,
MS_INVALIDATION).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class StructureState:
    bias: str                          # "bullish" | "bearish" | "range"
    last_bos: dict | None              # {"direction", "level", "ts"} or None
    last_choch: dict | None            # idem
    invalidation_level: float | None   # most-recent opposite pivot


def analyze_structure(
    highs: list[tuple[int, float]],
    lows: list[tuple[int, float]],
    current_price: float,
) -> StructureState:
    """`highs` / `lows` are (ts_or_idx, price) tuples, same shape as
    detect_pivots. Works on either index or timestamp keys — only ordering
    matters."""
    if len(highs) < 2 or len(lows) < 2:
        return StructureState(bias="range", last_bos=None, last_choch=None,
                              invalidation_level=None)

    # Bias reflects the MOST RECENT swing — not history-wide monotonicity.
    # Classic SMC: structure is bullish when the last HH > prior HH AND the
    # last HL > prior HL. Older pivots are historical; one stale outlier from
    # 3 swings ago shouldn't void a current trend shift. Using only the last
    # two pivots keeps MS responsive to the actual market state and makes it
    # fire as intended (otherwise range dominates on noisy real data).
    hh_seq = highs[-1][1] > highs[-2][1]
    ll_seq = lows[-1][1]  < lows[-2][1]
    hl_seq = lows[-1][1]  > lows[-2][1]
    lh_seq = highs[-1][1] < highs[-2][1]

    if hh_seq and hl_seq:
        bias = "bullish"
    elif ll_seq and lh_seq:
        bias = "bearish"
    else:
        bias = "range"

    last_bos: dict | None = None
    last_choch: dict | None = None
    invalidation: float | None = None

    if bias == "bullish":
        most_recent_hh = highs[-1]
        most_recent_hl = lows[-1]
        invalidation = most_recent_hl[1]
        # BOS = last confirmed HH; the pivot sequence itself proves it broke structure.
        last_bos = {"direction": "bullish", "level": most_recent_hh[1], "ts": most_recent_hh[0]}
        # CHoCH fires when current price is already below the most recent HL.
        if current_price < most_recent_hl[1]:
            last_choch = {"direction": "bearish", "level": most_recent_hl[1], "ts": most_recent_hl[0]}
    elif bias == "bearish":
        most_recent_ll = lows[-1]
        most_recent_lh = highs[-1]
        invalidation = most_recent_lh[1]
        # BOS = last confirmed LL.
        last_bos = {"direction": "bearish", "level": most_recent_ll[1], "ts": most_recent_ll[0]}
        # CHoCH fires when current price is already above the most recent LH.
        if current_price > most_recent_lh[1]:
            last_choch = {"direction": "bullish", "level": most_recent_lh[1], "ts": most_recent_lh[0]}

    return StructureState(
        bias=bias, last_bos=last_bos, last_choch=last_choch,
        invalidation_level=invalidation,
    )
