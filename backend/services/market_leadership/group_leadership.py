"""
Sector / Industry Group Leadership (methodology ml-gl-1.0.0).

Pure aggregation over already-computed per-member `StockRelativeStrength`
and lightweight member technical facts (above-50dma, near-252d-high). No
I/O — orchestration.py assembles the inputs. See architecture doc §4.2.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from services.market_leadership.configuration import (
    GROUP_MAX_MEMBER_WEIGHT, GROUP_MIN_SCORED_MEMBERS, GROUP_MIN_MEMBER_COVERAGE,
    GROUP_LEADING_SCORE, GROUP_LAGGING_SCORE, GROUP_BREADTH_LEADING,
    GROUP_IMPROVING_RATIO, GROUP_RS_STRONG_PCTL, GROUP_METHODOLOGY_VERSION,
    UNIVERSE_VERSION, CLASSIFICATION_SOURCE, CLASSIFICATION_VERSION,
    CLASSIFICATION_PIT_SAFE,
)
from services.market_leadership.contracts import (
    GroupLeadership, GroupState, LeadershipConcentration, RsTrend, ReasonCode,
)


class MemberFact:
    """Minimal per-member input group_leadership needs — decoupled from the
    full StockRelativeStrength contract so this module has no dependency
    direction on relative_strength.py beyond plain values."""

    __slots__ = (
        "symbol", "rs_percentile", "rs_trend", "market_cap",
        "above_50dma", "near_252d_high",
    )

    def __init__(self, symbol: str, rs_percentile: float | None, rs_trend: RsTrend,
                market_cap: float | None, above_50dma: bool | None,
                near_252d_high: bool | None):
        self.symbol = symbol
        self.rs_percentile = rs_percentile
        self.rs_trend = rs_trend
        self.market_cap = market_cap
        self.above_50dma = above_50dma
        self.near_252d_high = near_252d_high


def _capped_weights(members: list[MemberFact]) -> dict[str, float]:
    """Cap-proxy weights from market cap, capped at GROUP_MAX_MEMBER_WEIGHT
    per member; falls back to equal weight when no cap data exists."""
    caps = {m.symbol: m.market_cap for m in members if m.market_cap and m.market_cap > 0}
    if not caps:
        n = len(members)
        return {m.symbol: 1.0 / n for m in members} if n else {}
    total = sum(caps.values())
    raw = {sym: cap / total for sym, cap in caps.items()}
    # Iteratively redistribute weight above the cap to uncapped members —
    # standard capped-weight renormalization.
    weights = dict(raw)
    for _ in range(10):
        over = {s: w for s, w in weights.items() if w > GROUP_MAX_MEMBER_WEIGHT}
        if not over:
            break
        excess = sum(w - GROUP_MAX_MEMBER_WEIGHT for w in over.values())
        for s in over:
            weights[s] = GROUP_MAX_MEMBER_WEIGHT
        under = {s: w for s, w in weights.items() if s not in over}
        under_total = sum(under.values())
        if under_total <= 0:
            break
        for s in under:
            weights[s] += excess * (under[s] / under_total)
    # Members without cap data share equal weight from any residual.
    missing = [m.symbol for m in members if m.symbol not in weights]
    if missing:
        residual = max(0.0, 1.0 - sum(weights.values()))
        share = residual / len(missing) if missing else 0.0
        for s in missing:
            weights[s] = share
    return weights


def aggregate_group(members: list[MemberFact]) -> dict:
    """Breadth-aware aggregation stats for one group. Returns None-valued
    fields when there is insufficient scored membership — never a silent
    neutral fill-in."""
    scored = [m for m in members if m.rs_percentile is not None]
    n_members = len(members)
    n_scored = len(scored)
    coverage = (n_scored / n_members) if n_members else 0.0

    if n_scored < GROUP_MIN_SCORED_MEMBERS or coverage < GROUP_MIN_MEMBER_COVERAGE:
        return {
            "median_member_rs": None, "weighted_member_rs": None,
            "leadership_breadth": None, "participation_ratio": None,
            "improving_member_ratio": None, "new_high_participation": None,
            "coverage": round(coverage, 4), "n_scored": n_scored, "n_members": n_members,
            "insufficient": True,
        }

    pctls = np.array([m.rs_percentile for m in scored], dtype=float)
    median_rs = float(np.median(pctls))

    weights_all = _capped_weights(members)
    w_num = sum(weights_all.get(m.symbol, 0.0) * m.rs_percentile for m in scored)
    w_den = sum(weights_all.get(m.symbol, 0.0) for m in scored)
    weighted_rs = (w_num / w_den) if w_den > 0 else median_rs

    breadth = float(np.mean(pctls >= GROUP_RS_STRONG_PCTL))
    improving = [m for m in scored if m.rs_trend == RsTrend.IMPROVING]
    improving_ratio = len(improving) / n_scored

    with_ma = [m for m in scored if m.above_50dma is not None]
    participation = (
        float(np.mean([1.0 if m.above_50dma else 0.0 for m in with_ma])) if with_ma else None
    )
    with_hi = [m for m in scored if m.near_252d_high is not None]
    new_high = (
        float(np.mean([1.0 if m.near_252d_high else 0.0 for m in with_hi])) if with_hi else None
    )

    return {
        "median_member_rs": round(median_rs, 2),
        "weighted_member_rs": round(weighted_rs, 2),
        "leadership_breadth": round(breadth, 4),
        "participation_ratio": None if participation is None else round(participation, 4),
        "improving_member_ratio": round(improving_ratio, 4),
        "new_high_participation": None if new_high is None else round(new_high, 4),
        "coverage": round(coverage, 4), "n_scored": n_scored, "n_members": n_members,
        "insufficient": False,
    }


def classify_group_state(stats: dict, group_score: float | None, trend: str) -> GroupState:
    if stats["insufficient"] or group_score is None:
        return GroupState.INSUFFICIENT_DATA
    breadth = stats["leadership_breadth"] or 0.0
    improving_ratio = stats["improving_member_ratio"] or 0.0
    if group_score >= GROUP_LEADING_SCORE and breadth >= GROUP_BREADTH_LEADING:
        return GroupState.LEADING
    if trend == "UP" and improving_ratio >= GROUP_IMPROVING_RATIO:
        return GroupState.IMPROVING
    if trend == "DOWN" and group_score >= GROUP_LAGGING_SCORE:
        return GroupState.WEAKENING
    if group_score < GROUP_LAGGING_SCORE:
        return GroupState.LAGGING
    return GroupState.IMPROVING if trend == "UP" else GroupState.WEAKENING


def classify_concentration(stats: dict) -> LeadershipConcentration:
    if stats["insufficient"]:
        return LeadershipConcentration.INSUFFICIENT_DATA
    breadth = stats["leadership_breadth"] or 0.0
    median = stats["median_member_rs"]
    weighted = stats["weighted_member_rs"]
    gap = abs((weighted or 0) - (median or 0))
    if breadth >= 0.5 and gap < 10:
        return LeadershipConcentration.BROAD
    if breadth >= 0.25:
        return LeadershipConcentration.MODERATE if gap < 20 else LeadershipConcentration.CONCENTRATED
    return LeadershipConcentration.DETERIORATING if breadth < 0.15 else LeadershipConcentration.CONCENTRATED


def rank_groups(group_scores: dict[str, float | None]) -> dict[str, int | None]:
    """Descending rank (1 = strongest) among groups with a non-None score."""
    scored = {g: s for g, s in group_scores.items() if s is not None}
    order = sorted(scored, key=lambda g: -scored[g])
    ranks = {g: i + 1 for i, g in enumerate(order)}
    return {g: ranks.get(g) for g in group_scores}


def build_group_leadership(
    market: str, group_name: str, members: list[MemberFact], as_of: date,
    universe_id: str, group_rs_score: float | None, sector_rank: int | None,
    sector_rank_change: int | None, group_trend: str,
) -> GroupLeadership:
    stats = aggregate_group(members)
    state = classify_group_state(stats, group_rs_score, group_trend)
    concentration = classify_concentration(stats)

    reason_codes: list[str] = []
    if stats["insufficient"]:
        reason_codes.append(ReasonCode.LOW_COVERAGE.value)
    elif concentration == LeadershipConcentration.BROAD:
        reason_codes.append(ReasonCode.BROAD_GROUP_PARTICIPATION.value)
    elif concentration == LeadershipConcentration.CONCENTRATED:
        reason_codes.append(ReasonCode.CONCENTRATED_GROUP_LEADERSHIP.value)

    return GroupLeadership(
        market=market,
        group_kind="sector",
        sector_name=group_name,
        industry_name=group_name,  # v1: single classification level (see §4.2)
        sector_rs_score=group_rs_score,
        industry_rs_score=group_rs_score,
        sector_rank=sector_rank,
        industry_rank=sector_rank,
        sector_rank_change=sector_rank_change,
        industry_rank_change=sector_rank_change,
        leadership_breadth=stats["leadership_breadth"],
        participation_ratio=stats["participation_ratio"],
        median_member_rs=stats["median_member_rs"],
        weighted_member_rs=stats["weighted_member_rs"],
        improving_member_ratio=stats["improving_member_ratio"],
        new_high_participation=stats["new_high_participation"],
        group_trend=group_trend,
        group_state=state,
        concentration=concentration,
        member_count=stats["n_members"],
        scored_member_count=stats["n_scored"],
        calculation_as_of=as_of.isoformat(),
        classification_source=CLASSIFICATION_SOURCE,
        classification_version=CLASSIFICATION_VERSION,
        classification_pit_safe=CLASSIFICATION_PIT_SAFE,
        universe_id=universe_id,
        universe_version=UNIVERSE_VERSION,
        methodology_version=GROUP_METHODOLOGY_VERSION,
        reason_codes=reason_codes,
    )
