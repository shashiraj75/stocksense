"""
StockSense360 Growth Intelligence Engine v1
(SSDS-007, Epic 003 Sprint #003).

Answers exactly one question: "is this company's revenue, earnings, and
cash flow growing — and is that growth real, durable, and not bought at
shareholders' expense?" — deliberately distinct from Business Quality
("is this fundamentally an outstanding business?") and Financial
Strength ("could this survive a downturn?"). Per SSDS-007's Scope
Boundary section, this engine does NOT duplicate Business Quality's
existing growth-acceleration check (business_quality_engine.py's 3Y-vs-5Y
comparison) or Multibagger's growth checklist — both remain exactly
where they are.

v1 scope (per this sprint's explicit brief): Revenue Growth, Profit
Growth, EPS Trend, Growth Durability, Operating Profit Growth (where
available), Reinvestment Efficiency (where available), Margin Trend
(where available). Explicitly NOT implemented: Guidance Consistency,
Organic-vs-Acquisition Growth, naive Share Count Dilution — all three
named in SSDS-007/the India Feasibility Study as either lacking a data
source or requiring corporate-action-aware handling not yet built.

Provider-independent per SSDS-006: this module has no knowledge of
yfinance, screener.in, or SEC EDGAR. It reads only a pre-resolved
`fields` dict built by india_growth_adapter.py or us_growth_adapter.py —
mirroring exactly how financial_strength_engine.py never imports a
provider, only its own adapter layer does.
"""

import logging

from services.engine_contract import EngineResponse, Grade
from services.thresholds import GROWTH_INTELLIGENCE as GI

log = logging.getLogger(__name__)

# The four metrics confirmed available across virtually the entire
# universe in both the Design Study and the India Feasibility Study
# (revenue growth, profit growth, EPS trend, and the revenue series
# durability is computed from) — used for the REJECTED gate. The three
# "extended" metrics are structurally absent for some populations
# (banks/NBFCs in India) by design, not a data-quality failure, so they
# are excluded from the reject gate and instead drag down `confidence`
# directly (see compute_growth_intelligence's data_completeness_pct).
CORE_FIELDS = ["revenue_growth_3y_pct", "profit_growth_3y_pct", "eps_trend", "revenue_annual_series"]
# margin_trend_pct_change (not margin_annual_pct_series) is the field
# _margin_trend() actually scores — margin_annual_pct_series exists only
# as a raw-series passthrough for India debugging/future use, so it's
# deliberately excluded from completeness accounting to avoid penalizing
# the US adapter (which never separately populates that raw-series field,
# only the already-computed delta) for a field it was never meant to fill.
EXTENDED_FIELDS = ["operating_profit_growth_3y_pct", "reinvestment_capital_growth_3y_pct", "margin_trend_pct_change"]
ALL_FIELDS = CORE_FIELDS + EXTENDED_FIELDS


def _val(fields: dict, name: str):
    """Reads one field's value, or None if unavailable/absent — never
    fabricates, mirroring financial_strength_engine.py's identical helper."""
    rec = fields.get(name)
    if rec is None:
        return None
    return rec.get("value") if isinstance(rec, dict) else rec


def _revenue_growth(fields: dict) -> dict:
    """Revenue Growth (±15 base, ±18 with the acceleration bonus). Uses
    the 3Y CAGR as the primary signal; if a 5Y figure is also available,
    a 3Y > 5Y comparison adds a small acceleration bonus — mirroring
    Business Quality Engine's own growth-acceleration check conceptually,
    but computed independently here (not read from BQE) since SSDS-007's
    Scope Boundary keeps the two engines fully independent in v1. The
    bonus uses its own, higher cap (ACCELERATION_CAP) rather than the
    base STRONG cap — see thresholds.py's comment for why."""
    g3 = _val(fields, "revenue_growth_3y_pct")
    g5 = _val(fields, "revenue_growth_5y_pct")
    score = 0.0
    reasons = []
    if g3 is not None:
        if g3 >= GI.REVENUE_GROWTH_STRONG_MIN_PCT:
            score += 15
            reasons.append(f"Revenue growing {g3:.1f}%/yr (3Y) — strong top-line growth")
        elif g3 <= GI.REVENUE_GROWTH_WEAK_MAX_PCT:
            score -= 15
            reasons.append(f"Revenue growth {g3:.1f}%/yr (3Y) — flat or declining top line")
        if g5 is not None and g3 > g5:
            score = min(GI.REVENUE_GROWTH_ACCELERATION_CAP, score + GI.REVENUE_GROWTH_ACCELERATION_BONUS)
            reasons.append(f"Growth accelerating: 3Y ({g3:.1f}%) exceeds 5Y ({g5:.1f}%)")
    return {"score": max(-15, score), "revenue_growth_3y_pct": g3, "revenue_growth_5y_pct": g5, "reasons": reasons}


def _profit_growth(fields: dict) -> dict:
    """Profit Growth (±15 base, ±18 with the acceleration bonus). Same
    shape as Revenue Growth (including its separate acceleration cap —
    see that function's docstring), independently scored since profit
    and revenue growing at different rates is itself informative (margin
    expansion/compression), not redundant."""
    g3 = _val(fields, "profit_growth_3y_pct")
    g5 = _val(fields, "profit_growth_5y_pct")
    score = 0.0
    reasons = []
    if g3 is not None:
        if g3 >= GI.PROFIT_GROWTH_STRONG_MIN_PCT:
            score += 15
            reasons.append(f"Profit growing {g3:.1f}%/yr (3Y) — strong earnings growth")
        elif g3 <= GI.PROFIT_GROWTH_WEAK_MAX_PCT:
            score -= 15
            reasons.append(f"Profit growth {g3:.1f}%/yr (3Y) — flat or declining earnings")
        if g5 is not None and g3 > g5:
            score = min(GI.PROFIT_GROWTH_ACCELERATION_CAP, score + GI.PROFIT_GROWTH_ACCELERATION_BONUS)
            reasons.append(f"Profit growth accelerating: 3Y ({g3:.1f}%) exceeds 5Y ({g5:.1f}%)")
    return {"score": max(-15, score), "profit_growth_3y_pct": g3, "profit_growth_5y_pct": g5, "reasons": reasons}


def _eps_trend(fields: dict) -> dict:
    """EPS Trend (±8). Categorical (4-bucket) signal — smaller cap than
    the numeric growth categories since it's a coarser measurement."""
    trend = _val(fields, "eps_trend")
    score_map = {
        "accelerating": GI.EPS_TREND_ACCELERATING_SCORE,
        "mixed_positive": GI.EPS_TREND_MIXED_POSITIVE_SCORE,
        "mixed_negative": GI.EPS_TREND_MIXED_NEGATIVE_SCORE,
        "decelerating": GI.EPS_TREND_DECELERATING_SCORE,
    }
    score = score_map.get(trend, 0.0)
    reasons = [f"EPS trend: {trend}"] if trend else []
    return {"score": score, "eps_trend": trend, "reasons": reasons}


def _growth_durability(fields: dict) -> dict:
    """Growth Durability (±12). Coefficient of variation of YoY growth
    rates implied by the revenue history series — lower CV (consistent
    trend) scores positively, higher CV (erratic trend) scores negatively.
    The adapter is responsible for computing this CV value (via
    growth_utils.compute_coefficient_of_variation) and passing it as
    `revenue_growth_cv` — the engine itself stays pure arithmetic on
    whatever number it receives, never touching the raw series."""
    cv = _val(fields, "revenue_growth_cv")
    score = 0.0
    reasons = []
    if cv is not None:
        if cv <= GI.DURABILITY_LOW_CV:
            score = GI.DURABILITY_STRONG_SCORE
            reasons.append(f"Consistent growth trend (CV {cv:.2f}) — low year-to-year volatility")
        elif cv >= GI.DURABILITY_HIGH_CV:
            score = GI.DURABILITY_WEAK_SCORE
            reasons.append(f"Erratic growth trend (CV {cv:.2f}) — high year-to-year volatility")
    return {"score": score, "revenue_growth_cv": cv, "reasons": reasons}


def _operating_profit_growth(fields: dict) -> dict:
    """Operating Profit Growth (±12). Structurally unavailable for banks/
    NBFCs (confirmed by the India Feasibility Study) — the adapter passes
    None for this field rather than fabricating a value, and this
    function returns a neutral (0) score with no reasons, which is what
    drives the lower `confidence` for that population (see
    compute_growth_intelligence), not a rejection."""
    g3 = _val(fields, "operating_profit_growth_3y_pct")
    score = 0.0
    reasons = []
    if g3 is not None:
        if g3 >= GI.OPERATING_PROFIT_GROWTH_STRONG_MIN_PCT:
            score = GI.OPERATING_PROFIT_GROWTH_STRONG_SCORE
            reasons.append(f"Operating profit growing {g3:.1f}%/yr (3Y) — healthy operating leverage")
        elif g3 <= GI.OPERATING_PROFIT_GROWTH_WEAK_MAX_PCT:
            score = GI.OPERATING_PROFIT_GROWTH_WEAK_SCORE
            reasons.append(f"Operating profit growth {g3:.1f}%/yr (3Y) — flat or declining operating profit")
    return {"score": score, "operating_profit_growth_3y_pct": g3, "reasons": reasons}


def _reinvestment_efficiency(fields: dict) -> dict:
    """Reinvestment Efficiency (±8). Ratio of operating-profit growth to
    invested-capital growth — depends on Operating Profit Growth being
    available, so inherits the same bank/NBFC gap. A shrinking or flat
    capital base alongside growing operating profit (capital_growth <= 0
    and profit_growth > 0) is treated as maximally efficient (capital-light
    growth), not run through the ratio (which would divide by ~zero)."""
    op_growth = _val(fields, "operating_profit_growth_3y_pct")
    cap_growth = _val(fields, "reinvestment_capital_growth_3y_pct")
    score = 0.0
    reasons = []
    ratio = None
    if op_growth is not None and cap_growth is not None:
        if cap_growth <= 0:
            if op_growth > 0:
                score = GI.REINVESTMENT_EFFICIENCY_STRONG_SCORE
                reasons.append("Operating profit growing while invested capital is flat or shrinking — capital-light growth")
        else:
            ratio = round(op_growth / cap_growth, 2)
            if ratio >= GI.REINVESTMENT_EFFICIENCY_STRONG_MIN_RATIO:
                score = GI.REINVESTMENT_EFFICIENCY_STRONG_SCORE
                reasons.append(f"Operating profit growing {ratio:.2f}x faster than invested capital — efficient reinvestment")
            elif ratio <= GI.REINVESTMENT_EFFICIENCY_WEAK_MAX_RATIO:
                score = GI.REINVESTMENT_EFFICIENCY_WEAK_SCORE
                reasons.append(f"Invested capital growing faster than operating profit ({ratio:.2f}x) — inefficient reinvestment")
    return {"score": score, "reinvestment_efficiency_ratio": ratio, "reasons": reasons}


def _margin_trend(fields: dict) -> dict:
    """Margin Trend (±8). Percentage-point change in operating margin
    across the available history window — the adapter computes
    `margin_trend_pct_change` (latest minus earliest in the window) from
    the raw margin series; the engine only interprets the already-computed
    delta, staying provider/series-shape independent."""
    delta = _val(fields, "margin_trend_pct_change")
    score = 0.0
    reasons = []
    if delta is not None:
        if delta >= GI.MARGIN_EXPANSION_STRONG_MIN_PCT:
            score = GI.MARGIN_TREND_STRONG_SCORE
            reasons.append(f"Operating margin expanded {delta:+.1f}pp over the available history")
        elif delta <= GI.MARGIN_CONTRACTION_WEAK_MAX_PCT:
            score = GI.MARGIN_TREND_WEAK_SCORE
            reasons.append(f"Operating margin contracted {delta:+.1f}pp over the available history")
    return {"score": score, "margin_trend_pct_change": delta, "reasons": reasons}


def compute_growth_intelligence(symbol: str, fields: dict, sector_bucket: str = "", market: str = "US") -> dict:
    """
    The Growth Intelligence Engine's single public entry point.

    `fields` is a {field_name: value_or_{"value": ...}} dict built by
    india_growth_adapter.build_india_growth_fields() or
    us_growth_adapter.build_us_growth_fields() — never built by this
    function, which has no provider knowledge.

    Returns an EngineResponse (as a dict, via .to_dict()) per SSDS-007
    §EngineResponse Contract (inherited from engine_contract.py, shared
    with Business Quality and Financial Strength).
    """
    fields = fields or {}

    core_present = sum(1 for f in CORE_FIELDS if _val(fields, f) is not None)
    if core_present < GI.MIN_CORE_FIELDS_PRESENT:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=0.0,
            explanation="Insufficient growth data available to assess this company.",
            metadata={
                "engine": "growth_intelligence_engine",
                "engine_version": "v1",
                "market": market,
                "sector_bucket": sector_bucket,
                "rejection_reason": "insufficient_data",
                "missing_core_fields": [f for f in CORE_FIELDS if _val(fields, f) is None],
            },
        ).to_dict()

    all_present = sum(1 for f in ALL_FIELDS if _val(fields, f) is not None)
    data_completeness_pct = round(100 * all_present / len(ALL_FIELDS), 1)

    revenue = _revenue_growth(fields)
    profit = _profit_growth(fields)
    eps = _eps_trend(fields)
    durability = _growth_durability(fields)
    op_profit = _operating_profit_growth(fields)
    reinvestment = _reinvestment_efficiency(fields)
    margin = _margin_trend(fields)

    combined = (
        50
        + revenue["score"]
        + profit["score"]
        + eps["score"]
        + durability["score"]
        + op_profit["score"]
        + reinvestment["score"]
        + margin["score"]
    )
    score = round(max(0, min(100, combined)))

    if score >= GI.GRADE_STRONG_BUY_MIN:
        grade = Grade.STRONG_BUY
    elif score >= GI.GRADE_BUY_MIN:
        grade = Grade.BUY
    elif score >= GI.GRADE_HOLD_MIN:
        grade = Grade.HOLD
    elif score >= GI.GRADE_WATCH_MIN:
        grade = Grade.WATCH
    else:
        grade = Grade.AVOID

    categories = {
        "Revenue Growth": (revenue["score"], revenue["reasons"]),
        "Profit Growth": (profit["score"], profit["reasons"]),
        "EPS Trend": (eps["score"], eps["reasons"]),
        "Growth Durability": (durability["score"], durability["reasons"]),
        "Operating Profit Growth": (op_profit["score"], op_profit["reasons"]),
        "Reinvestment Efficiency": (reinvestment["score"], reinvestment["reasons"]),
        "Margin Trend": (margin["score"], margin["reasons"]),
    }
    ranked = sorted(categories.items(), key=lambda kv: kv[1][0], reverse=True)
    strengths = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked if val > 0 and reasons][:3]
    weaknesses = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked if val < 0 and reasons][-3:]

    risks = []
    if profit["profit_growth_3y_pct"] is not None and profit["profit_growth_3y_pct"] < GI.PROFIT_GROWTH_WEAK_MAX_PCT:
        risks.append(f"Profit growth {profit['profit_growth_3y_pct']:.1f}%/yr — earnings contracting")
    if durability["revenue_growth_cv"] is not None and durability["revenue_growth_cv"] >= GI.DURABILITY_HIGH_CV:
        risks.append(f"Highly volatile growth trend (CV {durability['revenue_growth_cv']:.2f}) — low forecast reliability")

    skipped_extended = [f for f in EXTENDED_FIELDS if _val(fields, f) is None]
    explanation = (
        f"Growth Intelligence Score {score}/100 ({grade.value}). "
        f"Revenue Growth contributed {revenue['score']:+.1f}, Profit Growth {profit['score']:+.1f}, "
        f"EPS Trend {eps['score']:+.1f}, Growth Durability {durability['score']:+.1f}, "
        f"Operating Profit Growth {op_profit['score']:+.1f}, Reinvestment Efficiency {reinvestment['score']:+.1f}, "
        f"Margin Trend {margin['score']:+.1f}."
    )
    if skipped_extended:
        explanation += f" {len(skipped_extended)} metric(s) unavailable for this company and excluded from scoring (not fabricated)."

    return EngineResponse(
        score=score,
        grade=grade,
        confidence=min(data_completeness_pct, 100.0),
        strengths=strengths,
        weaknesses=weaknesses,
        risks=risks,
        explanation=explanation,
        metadata={
            "engine": "growth_intelligence_engine",
            "engine_version": "v1",
            "market": market,
            "sector_bucket": sector_bucket,
            "data_completeness_pct": data_completeness_pct,
            "category_contributions": {name: val for name, (val, _) in categories.items()},
            "skipped_extended_fields": skipped_extended,
            "revenue_growth_3y_pct": revenue["revenue_growth_3y_pct"],
            "profit_growth_3y_pct": profit["profit_growth_3y_pct"],
            "eps_trend": eps["eps_trend"],
            "revenue_growth_cv": durability["revenue_growth_cv"],
            "operating_profit_growth_3y_pct": op_profit["operating_profit_growth_3y_pct"],
            "reinvestment_efficiency_ratio": reinvestment["reinvestment_efficiency_ratio"],
            "margin_trend_pct_change": margin["margin_trend_pct_change"],
        },
    ).to_dict()
