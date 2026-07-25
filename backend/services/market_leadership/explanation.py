"""
"Why Now?" structured explanation contract (methodology ml-ex-1.0.0).

Renders exclusively from verified structured fields already produced by
relative_strength.py / group_leadership.py / trend_lifecycle.py / breadth.py
— a fixed template registry keyed by state/reason codes. No LLM, no free
text generation, no numeric value that isn't present in the input contracts.
Wording never claims certainty or guaranteed returns (see architecture
doc §4.5).
"""
from __future__ import annotations

from services.market_leadership.configuration import EXPLANATION_CONTRACT_VERSION
from services.market_leadership.contracts import (
    StockRelativeStrength, GroupLeadership, TrendLifecycle, MarketBreadth,
    WhyNowExplanation, DataQualityStatus, TrendLifecycleState, ExtensionRisk,
    BreadthState, GroupState,
)

_TREND_PHRASE = {
    TrendLifecycleState.BASE_FORMING: "is forming a base",
    TrendLifecycleState.EARLY_ADVANCE: "is in an early advance",
    TrendLifecycleState.CONFIRMED_ADVANCE: "is in a confirmed advance",
    TrendLifecycleState.EXTENDED_ADVANCE: "is in an extended advance",
    TrendLifecycleState.DISTRIBUTION_RISK: "shows distribution-risk warning signs",
    TrendLifecycleState.DECLINING: "is in a decline",
    TrendLifecycleState.INSUFFICIENT_DATA: "has insufficient price history for a trend read",
}

_BREADTH_PHRASE = {
    BreadthState.HEALTHY: "market breadth is healthy",
    BreadthState.NARROWING: "market breadth is narrowing",
    BreadthState.DETERIORATING: "market breadth is deteriorating",
    BreadthState.RECOVERY: "market breadth is in recovery",
    BreadthState.WASHED_OUT: "market breadth is washed out",
    BreadthState.INSUFFICIENT_DATA: "market breadth data is insufficient",
}

_GROUP_PHRASE = {
    GroupState.LEADING: "its industry is leading with broad participation",
    GroupState.IMPROVING: "its industry is improving",
    GroupState.WEAKENING: "its industry is weakening",
    GroupState.LAGGING: "its industry is lagging",
    GroupState.INSUFFICIENT_DATA: "industry data is insufficient",
}


def _freshness_label(quality: DataQualityStatus) -> str:
    return {
        DataQualityStatus.OK: "current",
        DataQualityStatus.PARTIAL_HISTORY: "partial — some history missing",
        DataQualityStatus.STALE: "stale",
        DataQualityStatus.FAILED: "unavailable",
    }[quality]


def build_why_now_explanation(
    rs: StockRelativeStrength | None,
    group: GroupLeadership | None,
    trend: TrendLifecycle | None,
    breadth: MarketBreadth | None,
) -> WhyNowExplanation:
    supporting: list[str] = []
    caution: list[str] = []
    reason_codes: list[str] = []

    rs_context = industry_context = sector_context = trend_context = breadth_context = None

    if rs is not None:
        reason_codes.extend(rs.reason_codes)
        if rs.stock_rs_percentile is not None:
            rs_context = f"Relative Strength is {rs.stock_rs_score}/100 (percentile), trend {rs.rs_trend.value.lower()}."
            if rs.stock_rs_percentile >= 70:
                supporting.append(f"Stock RS is {rs.stock_rs_score} and {rs.rs_trend.value.lower()}.")
            elif rs.stock_rs_percentile <= 30:
                caution.append(f"Stock RS is weak at {rs.stock_rs_score}.")
        else:
            rs_context = "Relative Strength: insufficient data."
            caution.append("Relative Strength could not be computed (insufficient price history).")

    if group is not None:
        reason_codes.extend(group.reason_codes)
        phrase = _GROUP_PHRASE.get(group.group_state, "industry state is unknown")
        industry_context = f"Industry rank {group.industry_rank}, {phrase}."
        sector_context = f"Sector rank {group.sector_rank}, {phrase}."
        if group.group_state in (GroupState.LEADING, GroupState.IMPROVING):
            supporting.append(f"Its industry ranks {group.industry_rank} and {phrase}.")
        elif group.group_state in (GroupState.LAGGING, GroupState.WEAKENING):
            caution.append(f"Its industry {phrase}.")

    if trend is not None:
        reason_codes.extend(trend.reason_codes)
        trend_context = f"Trend Lifecycle: {trend.state.value} ({_TREND_PHRASE[trend.state]})."
        if trend.state in (TrendLifecycleState.EARLY_ADVANCE, TrendLifecycleState.CONFIRMED_ADVANCE):
            supporting.append(f"The stock {_TREND_PHRASE[trend.state]}.")
        elif trend.state == TrendLifecycleState.EXTENDED_ADVANCE:
            supporting.append(f"The stock {_TREND_PHRASE[trend.state]}.")
            caution.append("Price is extended above its medium-term trend, indicating elevated extension risk.")
        elif trend.state in (TrendLifecycleState.DISTRIBUTION_RISK, TrendLifecycleState.DECLINING):
            caution.append(f"The stock {_TREND_PHRASE[trend.state]}.")

    if breadth is not None:
        reason_codes.extend(breadth.reason_codes)
        breadth_context = f"Market breadth: {breadth.state.value}."
        phrase = _BREADTH_PHRASE.get(breadth.state, "market breadth is unknown")
        if breadth.state in (BreadthState.HEALTHY, BreadthState.RECOVERY):
            supporting.append(f"Market breadth remains {breadth.state.value.lower()}.")
        elif breadth.state in (BreadthState.DETERIORATING, BreadthState.WASHED_OUT, BreadthState.NARROWING):
            caution.append(f"However, {phrase}.")

    extension = trend.extension_risk if trend is not None else ExtensionRisk.UNKNOWN
    if extension == ExtensionRisk.HIGH and not any("extension" in c.lower() for c in caution):
        caution.append("Extension risk is high — price is well above its medium-term average.")

    quality = rs.data_quality_status if rs is not None else DataQualityStatus.FAILED
    calc_as_of = next(
        (x.calculation_as_of for x in (rs, group, trend, breadth) if x is not None), "unknown"
    )

    if not supporting and not caution:
        summary = "Insufficient structured evidence is available to summarize this stock right now."
        caution.append("No supporting or caution factors could be derived from available data.")
    else:
        parts = []
        if supporting:
            parts.append(" ".join(supporting))
        if caution:
            parts.append("However, " + " ".join(caution) if supporting else " ".join(caution))
        summary = " ".join(parts)

    return WhyNowExplanation(
        summary=summary,
        supporting_factors=supporting,
        caution_factors=caution,
        stock_rs_context=rs_context,
        industry_context=industry_context,
        sector_context=sector_context,
        trend_context=trend_context,
        breadth_context=breadth_context,
        extension_risk=extension.value,
        data_freshness=_freshness_label(quality),
        data_quality=quality.value,
        calculation_as_of=calc_as_of,
        methodology_version=EXPLANATION_CONTRACT_VERSION,
        reason_codes=sorted(set(reason_codes)),
    )
