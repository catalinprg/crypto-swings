"""Volume profile (composite + periodic naked POCs).

Composite profile: distribute each bar's volume uniformly across the bins
its [low, high] range covers. POC = bin with max volume. Value area =
expand outward from POC until 70% of total volume is captured; VAH/VAL =
top/bottom of that band.

Naked POC: periodic (daily / weekly / monthly) POC that price has NOT
revisited within ±TOUCH_ATR_MULT × ATR since the period closed. Strong magnet.

Bin width = BIN_ATR_MULT × ATR(14) → normalizes across BTC-at-$100k vs ETH-at-$3k.

Downstream consumption:
- Task 8 adapters convert VolumeProfile / NakedPOC into Level objects
  (sources: POC, VAH, VAL, HVN, LVN, NAKED_POC_D, NAKED_POC_W, NAKED_POC_M).
- Task 9 emit_payload.py receives per-TF aggregated bars from venue_aggregator
  and calls compute_profile / compute_naked_pocs per timeframe.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

from src.types import OHLC

BIN_ATR_MULT: float = 0.1      # bin width = ATR14 × this factor
VALUE_AREA_PCT: float = 0.70   # fraction of total volume the value area captures
TOUCH_ATR_MULT: float = 0.25   # naked-POC revisit radius = ATR14 × this factor


@dataclass(frozen=True)
class VolumeProfile:
    poc: float
    vah: float
    val: float
    hvn: list[float]    # high-volume nodes: local-max bins with z-score > 1.5
    lvn: list[float]    # low-volume nodes (rejection zones)
    bin_width: float


@dataclass(frozen=True)
class NakedPOC:
    price: float
    period_start_ts: int
    period_end_ts: int
    is_naked: bool
    distance_atr: float   # abs distance from current price in ATR units (always populated)


def compute_profile(bars: list[OHLC], atr_14: float) -> VolumeProfile:
    if not bars or atr_14 <= 0:
        return VolumeProfile(poc=0, vah=0, val=0, hvn=[], lvn=[], bin_width=0)
    lo = min(b.low for b in bars)
    hi = max(b.high for b in bars)
    bw = atr_14 * BIN_ATR_MULT
    if bw <= 0 or hi <= lo:
        return VolumeProfile(poc=(hi + lo) / 2, vah=hi, val=lo, hvn=[], lvn=[], bin_width=bw)
    n_bins = max(1, int(math.ceil((hi - lo) / bw)))
    mass = [0.0] * n_bins
    for b in bars:
        span = max(1e-12, b.high - b.low)
        vpp = b.volume / span  # uniform distribution across bar range — approximation; tick data not available
        # Contribute to every bin overlapped by [b.low, b.high]
        for i in range(n_bins):
            bin_lo = lo + i * bw
            bin_hi = bin_lo + bw
            overlap = max(0.0, min(bin_hi, b.high) - max(bin_lo, b.low))
            if overlap > 0:
                mass[i] += vpp * overlap
    # POC
    poc_idx = max(range(n_bins), key=lambda i: mass[i])
    poc_price = lo + (poc_idx + 0.5) * bw
    # Value area — expand from POC outward
    total = sum(mass)
    target = total * VALUE_AREA_PCT
    lo_i, hi_i = poc_idx, poc_idx
    acc = mass[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
        left = mass[lo_i - 1] if lo_i > 0 else -1
        right = mass[hi_i + 1] if hi_i < n_bins - 1 else -1
        # Ties go right (expand VAH before VAL): intentional upward bias,
        # consistent with CME/TT convention for symmetric cases.
        if right >= left:
            hi_i += 1
            acc += mass[hi_i]
        else:
            lo_i -= 1
            acc += mass[lo_i]
    vah = lo + (hi_i + 1) * bw
    val = lo + lo_i * bw
    # HVN / LVN via simple z-score on bin mass
    if len(mass) >= 3:
        mean = sum(mass) / len(mass)
        var = sum((m - mean) ** 2 for m in mass) / len(mass)
        sd = math.sqrt(var) if var > 0 else 0.0
        # HVN at z > 1.5 — only clear peaks qualify (right-skewed distributions need higher bar).
        # LVN at z < -1.0 — looser threshold; rejection zones are common, false positives are cheap.
        hvn = [lo + (i + 0.5) * bw for i, m in enumerate(mass) if sd > 0 and (m - mean) / sd > 1.5]
        lvn = [lo + (i + 0.5) * bw for i, m in enumerate(mass) if sd > 0 and (m - mean) / sd < -1.0]
    else:
        hvn, lvn = [], []
    return VolumeProfile(poc=poc_price, vah=vah, val=val, hvn=hvn, lvn=lvn, bin_width=bw)


def compute_naked_pocs(
    bars: list[OHLC], *, period_ms: int, lookback: int, atr_14: float,
) -> list[NakedPOC]:
    """Slice bars into `lookback` most recent complete periods of `period_ms`,
    compute per-period POC, flag as naked if price never re-visited within
    ±TOUCH_ATR_MULT × ATR since period close."""
    if not bars or atr_14 <= 0 or lookback <= 0:
        return []
    # Use one past the last bar's ts as the exclusive upper bound so that the
    # most-recent bar is included inside a period window rather than being the
    # sole post-period bar (which would always "visit" every POC).
    end_ts = bars[-1].ts + 1
    touch_radius = atr_14 * TOUCH_ATR_MULT
    out: list[NakedPOC] = []
    for k in range(1, lookback + 1):
        period_end = end_ts - (k - 1) * period_ms
        period_start = period_end - period_ms
        window = [b for b in bars if period_start <= b.ts < period_end]
        if not window:
            continue
        vp = compute_profile(window, atr_14)
        post = [b for b in bars if b.ts >= period_end]
        visited = any(
            abs((b.high + b.low) / 2 - vp.poc) <= touch_radius
            or (b.low <= vp.poc <= b.high)
            for b in post
        )
        out.append(NakedPOC(
            price=vp.poc,
            period_start_ts=period_start,
            period_end_ts=period_end,
            is_naked=not visited,
            distance_atr=abs(bars[-1].close - vp.poc) / atr_14,
        ))
    return out
