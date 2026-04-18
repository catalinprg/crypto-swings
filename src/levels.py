"""Unified multi-source confluence clustering.

Groups Levels from heterogeneous sources (fib, liquidity, VP, AVWAP, FVG,
OB, market structure) into zones. Zone score rewards DISTINCT source count
far more than raw level count — two sources agreeing is a stronger signal
than five fib retracements from the same swing.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Literal

from src.types import FibLevel, Level, Timeframe, TF_WEIGHTS

# Canonical single-source family groupings — rules out "two kinds of FIB"
# being counted as multi-source confluence.
SOURCE_FAMILY: dict[str, str] = {
    **{f"FIB_{r}": "FIB" for r in ("236", "382", "500", "618", "786", "1272", "1618")},
    "LIQ_BSL": "LIQ", "LIQ_SSL": "LIQ",
    "POC": "VP", "VAH": "VP", "VAL": "VP", "HVN": "VP", "LVN": "VP",
    "NAKED_POC_D": "NAKED_POC", "NAKED_POC_W": "NAKED_POC", "NAKED_POC_M": "NAKED_POC",
    # All AVWAP variants collapse to ONE family. Bands are ±σ derivatives of
    # the same VWAP line; swing/event anchors are just different anchor points
    # on the same construct. Counting them as independent families inflated
    # zone classification and squeezed out the structural_pivot signal (MS +
    # exactly 1 other family). Individual source tags stay visible in the
    # sources list so the analyst can still cite "AVWAP_BAND_2SD_DOWN" etc.
    "AVWAP_SESSION": "AVWAP", "AVWAP_WEEK": "AVWAP", "AVWAP_MONTH": "AVWAP",
    "AVWAP_SWING_HH": "AVWAP", "AVWAP_SWING_LL": "AVWAP",
    "AVWAP_EVENT": "AVWAP",
    "AVWAP_BAND_1SD_UP": "AVWAP", "AVWAP_BAND_1SD_DOWN": "AVWAP",
    "AVWAP_BAND_2SD_UP": "AVWAP", "AVWAP_BAND_2SD_DOWN": "AVWAP",
    "FVG_BULL": "FVG", "FVG_BEAR": "FVG",
    "OB_BULL": "OB", "OB_BEAR": "OB",
    "MS_BOS_LEVEL": "MS", "MS_CHOCH_LEVEL": "MS", "MS_INVALIDATION": "MS",
}

# Display priority for zone.sources — lower value surfaces first. Governs
# which source tags survive when the briefing truncates to the top-N per
# zone. MS and FIB are the load-bearing signals; AVWAP bands are context
# and should not crowd out the structural tags. Alphabetical sort left
# AVWAP_BAND_* dominating (A < M < F) which starved FIB/MS in briefings.
FAMILY_PRIORITY: dict[str, int] = {
    "MS":        0,
    "FIB":       1,
    "LIQ":       2,
    "FVG":       3,
    "OB":        4,
    "NAKED_POC": 5,
    "VP":        6,
    "AVWAP":     7,
}


def sort_sources_by_priority(sources: Iterable[str]) -> list[str]:
    """Order source tags by FAMILY_PRIORITY first, alphabetical within a
    family. Unknown families sort last."""
    def key(s: str) -> tuple[int, str]:
        fam = SOURCE_FAMILY.get(s, s)
        return (FAMILY_PRIORITY.get(fam, 99), s)
    return sorted(set(sources), key=key)

MAX_ZONE_WIDTH_MULTIPLIER = 2.0
FAMILY_BONUS = 3.0
HTF_WEEK_MULT = 1.25
HTF_MONTH_MULT = 1.5


@dataclass(frozen=True)
class MultiSourceZone:
    min_price: float
    max_price: float
    levels: tuple[Level, ...]
    source_count: int         # distinct families
    score: float
    classification: Literal["strong", "confluence", "structural_pivot", "level"]

    @property
    def mid(self) -> float:
        return (self.min_price + self.max_price) / 2


def cluster_levels(levels: Iterable[Level], radius: float) -> list[MultiSourceZone]:
    """Cluster by price using the same radius+width cap as fib clustering.
    Returns zones sorted by price ascending."""
    lvl_list = sorted(levels, key=lambda l: l.price)
    if not lvl_list:
        return []
    groups: list[list[Level]] = [[lvl_list[0]]]
    max_width = radius * MAX_ZONE_WIDTH_MULTIPLIER
    for l in lvl_list[1:]:
        near = l.price - groups[-1][-1].price <= radius
        within = l.price - groups[-1][0].price <= max_width
        if near and within:
            groups[-1].append(l)
        else:
            groups.append([l])
    return [_build_zone(g) for g in groups]


def _build_zone(group: list[Level]) -> MultiSourceZone:
    min_p = min(l.min_price for l in group)
    max_p = max(l.max_price for l in group)
    families = {SOURCE_FAMILY.get(l.source, l.source) for l in group}
    source_count = len(families)
    score = _score(group, families)
    cls = _classify(source_count, families)
    return MultiSourceZone(
        min_price=min_p, max_price=max_p,
        levels=tuple(group),
        source_count=source_count,
        score=round(score, 2),
        classification=cls,
    )


def _score(group: list[Level], families: set[str]) -> float:
    """Scoring:
      - +3 per distinct source family (heavily rewards orthogonal agreement)
      - +TF_WEIGHT * strength per individual level contribution
      - +HTF bonus multiplier applied to the zone total when any HTF source
        (1w, 1M) contributes: 1.25x for 1w, 1.5x for 1M.
    """
    base = FAMILY_BONUS * len(families)
    base += sum(TF_WEIGHTS.get(l.tf, 1) * l.strength for l in group)
    tfs = {l.tf for l in group}
    if "1M" in tfs:
        base *= HTF_MONTH_MULT
    elif "1w" in tfs:
        base *= HTF_WEEK_MULT
    return base


def _classify(
    source_count: int, families: set[str]
) -> Literal["strong", "confluence", "structural_pivot", "level"]:
    if source_count >= 3:
        return "strong"
    if source_count == 2:
        # Structural pivot = MS present with any other source
        if "MS" in families:
            return "structural_pivot"
        return "confluence"
    return "level"


def split_by_price(
    zones: list[MultiSourceZone], current_price: float
) -> tuple[list[MultiSourceZone], list[MultiSourceZone]]:
    """Support below, resistance above; straddling zones go by midpoint."""
    support, resistance = [], []
    for z in zones:
        if z.mid < current_price:
            support.append(z)
        else:
            resistance.append(z)
    support.sort(key=lambda z: z.score, reverse=True)
    resistance.sort(key=lambda z: z.score, reverse=True)
    return support, resistance


# ---- Source → Level adapters ----
#
# Intended strength ranking (design reference):
#   Market structure — MS_CHOCH=0.9, MS_BOS=0.8, MS_INVALIDATION=0.6
#   Volume profile  — POC=0.8, VAH/VAL=0.5, HVN=0.4, LVN=0.3
#   Naked POCs      — 0.7
#   AVWAP main      — 0.7; bands 1σ=0.4, 2σ=0.5
#   Order blocks    — unmitigated=0.7, stale=0.35
#   FVG             — unmitigated=0.6, stale=0.3
#   Fibs key ratios (0.382/0.5/0.618) — 0.6; secondary — 0.4
#   Liquidity pools — normalized by strength_score / POOL_STRENGTH_NORMALIZER

# Divisor that maps raw liquidity pool strength_score (0–30+) to 0–1.
POOL_STRENGTH_NORMALIZER: float = 30.0

_RATIO_TO_SRC: dict[float, str] = {
    0.236: "FIB_236", 0.382: "FIB_382", 0.5: "FIB_500",
    0.618: "FIB_618", 0.786: "FIB_786",
    1.272: "FIB_1272", 1.618: "FIB_1618",
}


def fibs_to_levels(fibs: list[FibLevel]) -> list[Level]:
    """Convert FibLevel objects to Level objects.

    Input:  list[FibLevel]  (from src.fib.compute_fib_levels)
    Output: Level per ratio; unknown ratios silently dropped.
    Sources emitted: FIB_236, FIB_382, FIB_500, FIB_618, FIB_786,
                     FIB_1272, FIB_1618
    """
    out: list[Level] = []
    for f in fibs:
        src = _RATIO_TO_SRC.get(f.ratio)
        if src is None:
            continue
        out.append(Level(
            price=f.price, min_price=f.price, max_price=f.price,
            source=src, tf=f.tf,
            strength=0.6 if f.ratio in (0.5, 0.618, 0.382) else 0.4,
            age_bars=0, meta={"ratio": f.ratio, "kind": f.kind},
        ))
    return out


def pools_to_levels(pools: dict[str, list[dict]], tf: Timeframe = "1d") -> list[Level]:
    """Convert liquidity pool dicts to Level objects; swept pools are dropped.

    Input:  pools dict with "buy_side" / "sell_side" keys
            (from src.liquidity_pools.detect_pools)
    Output: one Level per unswept pool entry.
    Sources emitted: LIQ_BSL, LIQ_SSL
    """
    out: list[Level] = []
    for side in ("buy_side", "sell_side"):
        for p in pools.get(side, []):
            if p.get("swept"):
                continue
            src = "LIQ_BSL" if p["type"] == "BSL" else "LIQ_SSL"
            rng = p["price_range"]
            out.append(Level(
                price=p["price"], min_price=rng[0], max_price=rng[1],
                source=src, tf=p["tfs"][0] if p["tfs"] else tf,
                strength=min(1.0, p["strength_score"] / POOL_STRENGTH_NORMALIZER),
                age_bars=p["age_hours"],
                meta={"touches": p["touches"], "tfs": p["tfs"]},
            ))
    return out


def profile_to_levels(profile: "VolumeProfile", *, tf: Timeframe) -> list[Level]:
    """Convert a VolumeProfile to Level objects.

    Input:  VolumeProfile  (from src.volume_profile.compute_profile)
    Output: POC + VAH + VAL + one Level per HVN/LVN.
    Sources emitted: POC, VAH, VAL, HVN, LVN
    """
    out: list[Level] = []
    out.append(Level(
        price=profile.poc, min_price=profile.poc, max_price=profile.poc,
        source="POC", tf=tf, strength=0.8, age_bars=0,
    ))
    out.append(Level(
        price=profile.vah, min_price=profile.vah, max_price=profile.vah,
        source="VAH", tf=tf, strength=0.5, age_bars=0,
    ))
    out.append(Level(
        price=profile.val, min_price=profile.val, max_price=profile.val,
        source="VAL", tf=tf, strength=0.5, age_bars=0,
    ))
    for h in profile.hvn:
        out.append(Level(
            price=h, min_price=h, max_price=h,
            source="HVN", tf=tf, strength=0.4, age_bars=0,
        ))
    for lv in profile.lvn:
        out.append(Level(
            price=lv, min_price=lv, max_price=lv,
            source="LVN", tf=tf, strength=0.3, age_bars=0,
        ))
    return out


def naked_pocs_to_levels(
    naked_list: list, *, period: str, tf: Timeframe
) -> list[Level]:
    """Convert NakedPOC objects to Level objects; non-naked entries are dropped.

    Input:  list[NakedPOC]  (from src.volume_profile.compute_naked_pocs)
    Output: one Level per is_naked=True entry.
    Sources emitted: NAKED_POC_D, NAKED_POC_W, NAKED_POC_M
    """
    src = {"D": "NAKED_POC_D", "W": "NAKED_POC_W", "M": "NAKED_POC_M"}[period]
    out: list[Level] = []
    for np_ in naked_list:
        if not np_.is_naked:
            continue
        out.append(Level(
            price=np_.price, min_price=np_.price, max_price=np_.price,
            source=src, tf=tf, strength=0.7,
            age_bars=0, meta={"distance_atr": np_.distance_atr},
        ))
    return out


def avwap_to_levels(avwaps: list, *, tf: Timeframe) -> list[Level]:
    """Convert AnchoredVwap objects to Level objects at their latest values.

    Emits one main Level at the last non-NaN VWAP value, plus up to four
    band Levels (±1σ, ±2σ) when those band series are populated.

    Input:  list[AnchoredVwap]  (from src.avwap.compute_avwap)
    Output: main + band Levels per anchor.
    Sources emitted: anchor_type value (e.g. AVWAP_WEEK) plus
                     AVWAP_BAND_1SD_UP, AVWAP_BAND_1SD_DOWN,
                     AVWAP_BAND_2SD_UP, AVWAP_BAND_2SD_DOWN
    """
    out: list[Level] = []
    for a in avwaps:
        if not a.vwap or all(x != x for x in a.vwap):
            continue
        last = next((v for v in reversed(a.vwap) if v == v), None)
        if last is None:
            continue
        out.append(Level(
            price=last, min_price=last, max_price=last,
            source=a.anchor_type, tf=tf, strength=0.7, age_bars=0,
        ))
        u1 = next((v for v in reversed(a.upper_1sd) if v == v), None)
        l1 = next((v for v in reversed(a.lower_1sd) if v == v), None)
        u2 = next((v for v in reversed(a.upper_2sd) if v == v), None)
        l2 = next((v for v in reversed(a.lower_2sd) if v == v), None)
        if u1 is not None:
            out.append(Level(price=u1, min_price=u1, max_price=u1,
                             source="AVWAP_BAND_1SD_UP", tf=tf, strength=0.4, age_bars=0))
        if l1 is not None:
            out.append(Level(price=l1, min_price=l1, max_price=l1,
                             source="AVWAP_BAND_1SD_DOWN", tf=tf, strength=0.4, age_bars=0))
        if u2 is not None:
            out.append(Level(price=u2, min_price=u2, max_price=u2,
                             source="AVWAP_BAND_2SD_UP", tf=tf, strength=0.5, age_bars=0))
        if l2 is not None:
            out.append(Level(price=l2, min_price=l2, max_price=l2,
                             source="AVWAP_BAND_2SD_DOWN", tf=tf, strength=0.5, age_bars=0))
    return out


def fvgs_to_levels(fvgs: list) -> list[Level]:
    """Convert FVG objects to Level objects; mitigated FVGs are dropped.

    Stale but unmitigated FVGs pass through with strength scaled down (0.3
    vs 0.6 for fresh).

    Input:  list[FVG]  (from src.fvg.detect_fvgs)
    Output: one Level per unmitigated FVG; price = zone midpoint.
    Sources emitted: FVG_BULL, FVG_BEAR
    """
    out: list[Level] = []
    for f in fvgs:
        if f.mitigated:
            continue
        mid = (f.lo + f.hi) / 2
        strength = 0.6 if not f.stale else 0.3
        out.append(Level(
            price=mid, min_price=f.lo, max_price=f.hi,
            source=f.type, tf=f.tf, strength=strength,
            age_bars=f.age_bars, meta={"stale": f.stale},
        ))
    return out


def obs_to_levels(obs: list) -> list[Level]:
    """Convert OrderBlock objects to Level objects; mitigated OBs are dropped.

    Stale but unmitigated OBs pass through with strength scaled down (0.35
    vs 0.7 for fresh).

    Input:  list[OrderBlock]  (from src.order_blocks.detect_order_blocks)
    Output: one Level per unmitigated OB; price = zone midpoint.
    Sources emitted: OB_BULL, OB_BEAR
    """
    out: list[Level] = []
    for o in obs:
        if o.mitigated:
            continue
        mid = (o.lo + o.hi) / 2
        strength = 0.7 if not o.stale else 0.35
        out.append(Level(
            price=mid, min_price=o.lo, max_price=o.hi,
            source=o.type, tf=o.tf, strength=strength,
            age_bars=o.age_bars, meta={"stale": o.stale},
        ))
    return out


def structure_to_levels(state: "StructureState", *, tf: Timeframe) -> list[Level]:
    """Convert a StructureState to Level objects.

    Emits up to three Levels: BOS level, CHoCH level, invalidation level.
    Direction ("bullish" | "bearish") is preserved in meta["direction"] so
    downstream consumers can distinguish bias.

    Input:  StructureState  (from src.market_structure.analyze_structure)
    Output: up to 3 Levels depending on which fields are populated.
    Sources emitted: MS_BOS_LEVEL, MS_CHOCH_LEVEL, MS_INVALIDATION
    """
    out: list[Level] = []
    if state.last_bos:
        lvl = state.last_bos["level"]
        out.append(Level(
            price=lvl, min_price=lvl, max_price=lvl,
            source="MS_BOS_LEVEL", tf=tf, strength=0.8, age_bars=0,
            meta={"direction": state.last_bos["direction"]},
        ))
    if state.last_choch:
        lvl = state.last_choch["level"]
        out.append(Level(
            price=lvl, min_price=lvl, max_price=lvl,
            source="MS_CHOCH_LEVEL", tf=tf, strength=0.9, age_bars=0,
            meta={"direction": state.last_choch["direction"]},
        ))
    if state.invalidation_level is not None:
        out.append(Level(
            price=state.invalidation_level,
            min_price=state.invalidation_level, max_price=state.invalidation_level,
            source="MS_INVALIDATION", tf=tf, strength=0.6, age_bars=0,
        ))
    return out
