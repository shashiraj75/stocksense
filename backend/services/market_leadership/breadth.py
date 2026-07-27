"""
Market Breadth Context (methodology ml-br-1.0.0).

Pure aggregation over per-member technical facts already computed for the
as-of session. No I/O, no network. See architecture doc §4.4 for the state
precedence table and rationale.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from services.market_leadership.configuration import (
    BREADTH_MIN_COVERAGE, BREADTH_MIN_SESSIONS, BREADTH_HEALTHY_ABOVE_200,
    BREADTH_HEALTHY_ABOVE_50, BREADTH_NARROW_ABOVE_50, BREADTH_RECOVERY_FROM,
    BREADTH_RECOVERY_TO, BREADTH_RECOVERY_AD, BREADTH_WASHOUT_ABOVE_200,
    BREADTH_WASHOUT_NL_NH_RATIO, BREADTH_METHODOLOGY_VERSION, UNIVERSE_VERSION,
)
from services.market_leadership.contracts import (
    MarketBreadth, BreadthComponent, BreadthState, RsTrend, ReasonCode,
)


class BreadthMemberFact:
    __slots__ = (
        "symbol", "sessions", "above_20dma", "above_50dma", "above_200dma",
        "is_new_high_63d", "is_new_low_63d", "rs_trend", "return_21d",
    )

    def __init__(self, symbol: str, sessions: int, above_20dma: bool | None,
                above_50dma: bool | None, above_200dma: bool | None,
                is_new_high_63d: bool | None, is_new_low_63d: bool | None,
                rs_trend: RsTrend | None, return_21d: float | None):
        self.symbol = symbol
        self.sessions = sessions
        self.above_20dma = above_20dma
        self.above_50dma = above_50dma
        self.above_200dma = above_200dma
        self.is_new_high_63d = is_new_high_63d
        self.is_new_low_63d = is_new_low_63d
        self.rs_trend = rs_trend
        self.return_21d = return_21d


def _ratio(flags: list[bool | None]) -> tuple[float | None, int, int]:
    usable = [f for f in flags if f is not None]
    if not usable:
        return None, 0, len(flags)
    return (sum(1 for f in usable if f) / len(usable)), len(usable), len(flags)


def compute_breadth_components(
    members: list[BreadthMemberFact], benchmark_return_21d: float | None,
) -> dict:
    universe_size = len(members)
    eligible = [m for m in members if m.sessions >= BREADTH_MIN_SESSIONS]
    scored_count = len(eligible)
    coverage = (scored_count / universe_size) if universe_size else 0.0

    if universe_size == 0 or coverage < BREADTH_MIN_COVERAGE:
        return {"insufficient": True, "coverage": round(coverage, 4),
                "universe_size": universe_size, "scored_count": scored_count}

    pct20, n20, d20 = _ratio([m.above_20dma for m in eligible])
    pct50, n50, d50 = _ratio([m.above_50dma for m in eligible])
    pct200, n200, d200 = _ratio([m.above_200dma for m in eligible])

    advances = sum(1 for m in eligible if (m.return_21d or 0) > 0)
    declines = sum(1 for m in eligible if (m.return_21d or 0) < 0)
    ad_ratio = (advances / declines) if declines > 0 else (float("inf") if advances > 0 else None)

    new_highs = sum(1 for m in eligible if m.is_new_high_63d)
    new_lows = sum(1 for m in eligible if m.is_new_low_63d)

    improving = [m for m in eligible if m.rs_trend == RsTrend.IMPROVING]
    rs_with_trend = [m for m in eligible if m.rs_trend is not None]
    improving_ratio = (len(improving) / len(rs_with_trend)) if rs_with_trend else None

    member_returns = [m.return_21d for m in eligible if m.return_21d is not None]
    median_member_return = float(np.median(member_returns)) if member_returns else None
    divergence = (
        benchmark_return_21d is not None and median_member_return is not None
        and np.sign(benchmark_return_21d) != np.sign(median_member_return)
        and abs(benchmark_return_21d) > 0.1
    )

    return {
        "insufficient": False,
        "coverage": round(coverage, 4),
        "universe_size": universe_size, "scored_count": scored_count,
        "pct_above_20dma": None if pct20 is None else round(pct20, 4),
        "pct_above_50dma": None if pct50 is None else round(pct50, 4),
        "pct_above_200dma": None if pct200 is None else round(pct200, 4),
        "n20": n20, "d20": d20, "n50": n50, "d50": d50, "n200": n200, "d200": d200,
        "advance_decline_ratio_21d": None if ad_ratio is None else round(min(ad_ratio, 999.0), 3),
        "new_highs_63d": new_highs, "new_lows_63d": new_lows,
        "improving_rs_ratio": None if improving_ratio is None else round(improving_ratio, 4),
        "divergence": bool(divergence),
    }


def classify_breadth_state(stats: dict) -> BreadthState:
    if stats["insufficient"]:
        return BreadthState.INSUFFICIENT_DATA
    above200 = stats["pct_above_200dma"] or 0.0
    above50 = stats["pct_above_50dma"] or 0.0
    ad = stats["advance_decline_ratio_21d"]
    new_highs = stats["new_highs_63d"]
    new_lows = stats["new_lows_63d"]

    # Fixed precedence: WASHED_OUT > RECOVERY > DETERIORATING > NARROWING > HEALTHY
    if above200 <= BREADTH_WASHOUT_ABOVE_200 and new_lows >= BREADTH_WASHOUT_NL_NH_RATIO * max(new_highs, 1):
        return BreadthState.WASHED_OUT
    if (
        BREADTH_RECOVERY_FROM <= (stats["pct_above_20dma"] or 0.0) < BREADTH_RECOVERY_TO
        and ad is not None and ad >= BREADTH_RECOVERY_AD
    ):
        return BreadthState.RECOVERY
    if ad is not None and ad < 1.0 and new_lows > new_highs and above50 < BREADTH_NARROW_ABOVE_50:
        return BreadthState.DETERIORATING
    if stats["divergence"] or above50 < BREADTH_NARROW_ABOVE_50:
        return BreadthState.NARROWING
    if above200 >= BREADTH_HEALTHY_ABOVE_200 and above50 >= BREADTH_HEALTHY_ABOVE_50 and (ad is None or ad >= 1.0):
        return BreadthState.HEALTHY
    return BreadthState.NARROWING


def build_market_breadth(
    market: str, scope: str, members: list[BreadthMemberFact],
    benchmark_return_21d: float | None, as_of: date, expected_session: date,
    universe_id: str, direction_5d: str = "UNKNOWN",
) -> MarketBreadth:
    stats = compute_breadth_components(members, benchmark_return_21d)
    state = classify_breadth_state(stats)

    reasons: list[str] = []
    if stats["insufficient"]:
        reasons.append(ReasonCode.LOW_COVERAGE.value)
    if stats.get("divergence"):
        reasons.append(ReasonCode.BREADTH_DIVERGENCE.value)

    components = []
    if not stats["insufficient"]:
        components = [
            BreadthComponent("pct_above_20dma", stats["pct_above_20dma"], stats["n20"], stats["d20"],
                             round(stats["n20"] / stats["d20"], 4) if stats["d20"] else None),
            BreadthComponent("pct_above_50dma", stats["pct_above_50dma"], stats["n50"], stats["d50"],
                             round(stats["n50"] / stats["d50"], 4) if stats["d50"] else None),
            BreadthComponent("pct_above_200dma", stats["pct_above_200dma"], stats["n200"], stats["d200"],
                             round(stats["n200"] / stats["d200"], 4) if stats["d200"] else None),
        ]

    return MarketBreadth(
        market=market,
        scope=scope,
        state=state,
        pct_above_20dma=stats.get("pct_above_20dma"),
        pct_above_50dma=stats.get("pct_above_50dma"),
        pct_above_200dma=stats.get("pct_above_200dma"),
        advance_decline_ratio_21d=stats.get("advance_decline_ratio_21d"),
        new_highs_63d=stats.get("new_highs_63d"),
        new_lows_63d=stats.get("new_lows_63d"),
        improving_rs_ratio=stats.get("improving_rs_ratio"),
        direction_5d=direction_5d,
        components=components,
        coverage=stats["coverage"],
        universe_size=stats["universe_size"],
        scored_count=stats["scored_count"],
        expected_session=expected_session.isoformat(),
        calculation_as_of=as_of.isoformat(),
        universe_id=universe_id,
        universe_version=UNIVERSE_VERSION,
        methodology_version=BREADTH_METHODOLOGY_VERSION,
        reason_codes=reasons,
    )
