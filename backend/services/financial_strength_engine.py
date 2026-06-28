"""
StockSense360 Financial Strength Intelligence Engine v1
(SSDS-005, SSDS-006, Epic 002 Sprint #008).

Answers exactly one question: "could this company survive a downturn,
service its obligations, and avoid distress in the next 1-3 years?" —
deliberately distinct from the Business Quality Engine's "is this a
great business worth owning for decades?" Per SSDS-005's Scope Boundary
section, this engine does NOT compute Altman Z-Score, Sloan Accruals,
Beneish M-Score, Piotroski F-Score, the Buffett/Munger checklist, Cash
Conversion Ratio, Asset Turnover, or Working Capital Trend — all of
those remain exclusively Business Quality Intelligence's territory. If
any of those values are ever needed here, they must be READ from
Business Quality's existing output, never recomputed — no such read
exists in this v1 (the two engines run fully independently for now).

v1 scope (Sprint #008): non-FINANCIAL, non-REAL_ESTATE companies only.
Per SSDS-006's hard architectural rule, this engine is provider-
independent — it has no knowledge of SEC EDGAR, yfinance, or the
provider-precedence module. It reads only a pre-resolved `fields` dict
shaped exactly like services/us_provider_precedence.resolve_field()'s
own per-field output (value + confidence + provenance), supplied by
services/us_financial_strength_adapter.py. This mirrors exactly how
business_quality_engine.py never imports screener_data or yfinance
directly — only the adapter layer does provider I/O.

Implements 3 of SSDS-005's 5 scoring categories' full proposed metric
sets and 2 with a named, deliberate reduction (see each category's own
docstring below) — every omission is a stated Known Limitation, not a
silent gap, consistent with this engagement's standing practice.
"""

import logging

from services.engine_contract import EngineResponse, Grade
from services.thresholds import FINANCIAL_STRENGTH as FS

log = logging.getLogger(__name__)

# v1 explicitly excludes these sector buckets — Sprint #005/#007 confirmed
# both share a structural data gap (current_assets/current_liabilities/
# ebit/short_term_debt/long_term_debt absent on both EDGAR and yfinance)
# that no precedence rule or fallback chain can close; a sector-specific
# substitute computation is required and is explicitly out of this
# sprint's scope (per this sprint's own "Do not implement: Banks, NBFCs,
# Insurance, REITs" rule). REAL_ESTATE is sector_quality_applicability.py's
# existing taxonomy bucket, reused here as the REIT proxy classification —
# not a new taxonomy, per SES-002 §2.
V1_EXCLUDED_SECTOR_BUCKETS = {"FINANCIAL", "REAL_ESTATE"}

# The 16 SSDS-005-required unified fields (SSDS-006 §5) — every one is
# Mandatory for v1's data-completeness gate; none has been found, in
# Sprint #007's evidence, to need Optional/sector-adjusted treatment for
# the non-FINANCIAL/REAL_ESTATE companies this v1 actually scores.
MANDATORY_FIELDS = [
    "revenue", "net_income", "ebit", "interest_expense", "cash_and_equivalents",
    "current_assets", "current_liabilities", "total_assets", "total_liabilities",
    "short_term_debt", "long_term_debt", "total_debt", "operating_cash_flow",
    "capital_expenditure", "free_cash_flow", "shareholders_equity",
]


def _val(fields: dict, name: str):
    """Reads one field's resolved value, or None if unavailable/absent —
    never fabricates, per SSDS-003/SSDS-005's shared missing-data rule."""
    rec = fields.get(name)
    return rec.get("value") if rec else None


def _ratio(numerator, denominator):
    """Safe ratio — returns None (not zero, not an exception) if either
    side is unusable, so a None propagates as 'cannot compute this
    sub-metric' rather than a silently wrong zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _liquidity_adequacy(fields: dict) -> dict:
    """
    Liquidity Adequacy (±20). Implements Current Ratio and Cash Ratio —
    both fully computable from the 16-field unified schema. Does NOT
    implement Quick Ratio or Cash Runway (SSDS-005's own proposed
    metrics): Quick Ratio needs a receivables/inventory split the
    unified schema doesn't carry, and Cash Runway needs a monthly
    operating-expense figure this schema also doesn't carry — both
    named as Known Limitations (Sprint #008 report), not approximated
    with an untested derivation.
    """
    current_assets = _val(fields, "current_assets")
    current_liabilities = _val(fields, "current_liabilities")
    cash = _val(fields, "cash_and_equivalents")

    current_ratio = _ratio(current_assets, current_liabilities)
    cash_ratio = _ratio(cash, current_liabilities)

    score = 0.0
    reasons = []
    if current_ratio is not None:
        if current_ratio >= FS.CURRENT_RATIO_STRONG_MIN:
            score += 10
            reasons.append(f"Current ratio {current_ratio:.2f}x — comfortably covers current liabilities")
        elif current_ratio <= FS.CURRENT_RATIO_WEAK_MAX:
            score -= 10
            reasons.append(f"Current ratio {current_ratio:.2f}x — current liabilities exceed current assets")
    if cash_ratio is not None:
        if cash_ratio >= FS.CASH_RATIO_STRONG_MIN:
            score += 6
            reasons.append(f"Cash ratio {cash_ratio:.2f}x — cash alone covers a strong share of current liabilities")
        elif cash_ratio <= FS.CASH_RATIO_THIN_MAX:
            score -= 6
            reasons.append(f"Cash ratio {cash_ratio:.2f}x — thin cash cushion against current liabilities")

    return {
        "score": max(-20, min(20, score)),
        "current_ratio": current_ratio,
        "cash_ratio": cash_ratio,
        "reasons": reasons,
    }


def _leverage_and_capital_structure(fields: dict) -> dict:
    """
    Leverage & Capital Structure (±20). Implements Debt-to-Equity and
    short-term-debt-share-of-total-debt — both fully computable. Does
    NOT implement Net Debt/EBITDA (SSDS-005's own proposed metric): the
    unified schema carries EBIT, not EBITDA, and no D&A field exists to
    bridge the two — named as a Known Limitation, not derived from an
    unvalidated D&A assumption.
    """
    total_debt = _val(fields, "total_debt")
    equity = _val(fields, "shareholders_equity")
    short_term_debt = _val(fields, "short_term_debt")

    debt_to_equity_pct = None
    if total_debt is not None and equity:
        debt_to_equity_pct = (total_debt / equity) * 100

    short_term_share_pct = None
    if short_term_debt is not None and total_debt:
        short_term_share_pct = (short_term_debt / total_debt) * 100

    score = 0.0
    reasons = []
    if debt_to_equity_pct is not None:
        if debt_to_equity_pct < FS.DEBT_TO_EQUITY_ELEVATED_MIN_PCT:
            score += 10
            reasons.append(f"Debt-to-equity {debt_to_equity_pct:.0f}% — below the elevated-leverage tier")
        elif debt_to_equity_pct >= FS.DEBT_TO_EQUITY_SEVERE_MIN_PCT:
            score -= 12
            reasons.append(f"Debt-to-equity {debt_to_equity_pct:.0f}% — severe leverage")
        elif debt_to_equity_pct >= FS.DEBT_TO_EQUITY_ELEVATED_MIN_PCT:
            score -= 6
            reasons.append(f"Debt-to-equity {debt_to_equity_pct:.0f}% — elevated leverage")
    if short_term_share_pct is not None and short_term_share_pct >= FS.SHORT_TERM_DEBT_SHARE_ELEVATED_MIN_PCT:
        score -= 5
        reasons.append(f"{short_term_share_pct:.0f}% of total debt is short-term — refinancing-risk signal")

    return {
        "score": max(-20, min(20, score)),
        "debt_to_equity_pct": debt_to_equity_pct,
        "short_term_debt_share_pct": short_term_share_pct,
        "reasons": reasons,
    }


def _debt_servicing_capacity(fields: dict) -> dict:
    """
    Debt-Servicing Capacity (±20). Implements Interest Coverage (level)
    and the Earnings Shock stress scenario (EBIT down
    FS.STRESS_EARNINGS_SHOCK_PCT%, interest coverage recomputed). Does
    NOT implement the Revenue Shock or Liquidity Shock scenarios SSDS-005
    also proposed — both need a cost-structure (fixed vs. variable) or
    debt-maturity-schedule breakdown the unified schema doesn't carry;
    named as Known Limitations, not approximated.
    """
    ebit = _val(fields, "ebit")
    interest_expense = _val(fields, "interest_expense")

    interest_coverage = _ratio(ebit, interest_expense)

    score = 0.0
    reasons = []
    if interest_coverage is not None:
        if interest_coverage >= FS.INTEREST_COVERAGE_STRONG_MIN:
            score += 12
            reasons.append(f"Interest coverage {interest_coverage:.1f}x — comfortably covers interest")
        elif interest_coverage <= FS.INTEREST_COVERAGE_WEAK_MAX:
            score -= 12
            reasons.append(f"Interest coverage {interest_coverage:.1f}x — earnings barely cover interest")

    stress_result = None
    if ebit is not None and interest_expense:
        shocked_ebit = ebit * (1 - FS.STRESS_EARNINGS_SHOCK_PCT / 100)
        shocked_coverage = _ratio(shocked_ebit, interest_expense)
        passed = bool(shocked_coverage is not None and shocked_coverage >= FS.STRESS_INTEREST_COVERAGE_PASS_MIN)
        stress_result = {
            "scenario": "earnings_shock",
            "shock_pct": FS.STRESS_EARNINGS_SHOCK_PCT,
            "shocked_ebit": shocked_ebit,
            "shocked_interest_coverage": shocked_coverage,
            "passed": passed,
        }
        if not passed:
            score -= 8
            reasons.append(
                f"Earnings Shock (EBIT -{FS.STRESS_EARNINGS_SHOCK_PCT:.0f}%): interest coverage would fall to "
                f"{shocked_coverage:.1f}x — below the {FS.STRESS_INTEREST_COVERAGE_PASS_MIN:.1f}x survival bar"
            )

    return {
        "score": max(-20, min(20, score)),
        "interest_coverage": interest_coverage,
        "stress_result": stress_result,
        "reasons": reasons,
    }


def _balance_sheet_resilience(fields: dict) -> dict:
    """
    Balance Sheet Resilience (±15). Implements the Equity Ratio
    (shareholders_equity / total_assets). Does NOT implement off-balance-
    sheet/contingent-liability awareness (SSDS-005's own proposed
    metric) — no such field exists anywhere in the unified schema;
    named as a Known Limitation.
    """
    equity = _val(fields, "shareholders_equity")
    total_assets = _val(fields, "total_assets")
    equity_ratio_pct = None
    if equity is not None and total_assets:
        equity_ratio_pct = (equity / total_assets) * 100

    score = 0.0
    reasons = []
    if equity_ratio_pct is not None:
        if equity_ratio_pct >= FS.EQUITY_RATIO_STRONG_MIN_PCT:
            score += 10
            reasons.append(f"Equity ratio {equity_ratio_pct:.0f}% — strong equity cushion")
        elif equity_ratio_pct <= FS.EQUITY_RATIO_THIN_MAX_PCT:
            score -= 10
            reasons.append(f"Equity ratio {equity_ratio_pct:.0f}% — thin equity cushion")

    return {
        "score": max(-15, min(15, score)),
        "equity_ratio_pct": equity_ratio_pct,
        "reasons": reasons,
    }


def _cash_flow_durability(fields: dict) -> dict:
    """
    Cash Flow Durability Under Stress (±15). Implements Free Cash Flow
    Margin (free_cash_flow / revenue) as the durability signal — distinct
    from Business Quality's Cash Conversion Ratio (OCF/Net Income),
    which this engine never recomputes (SSDS-005 Scope Boundary).
    """
    fcf = _val(fields, "free_cash_flow")
    revenue = _val(fields, "revenue")
    fcf_margin_pct = None
    if fcf is not None and revenue:
        fcf_margin_pct = (fcf / revenue) * 100

    score = 0.0
    reasons = []
    if fcf_margin_pct is not None:
        if fcf_margin_pct >= FS.FCF_MARGIN_STRONG_MIN_PCT:
            score += 10
            reasons.append(f"Free cash flow margin {fcf_margin_pct:.1f}% — healthy, self-funding")
        elif fcf_margin_pct <= FS.FCF_MARGIN_NEGATIVE_MAX_PCT:
            score -= 10
            reasons.append(f"Free cash flow margin {fcf_margin_pct:.1f}% — cash-consuming, not cash-generating")

    return {
        "score": max(-15, min(15, score)),
        "fcf_margin_pct": fcf_margin_pct,
        "reasons": reasons,
    }


def compute_financial_strength(symbol: str, fields: dict, sector_bucket: str, market: str = "US") -> dict:
    """
    The Financial Strength Engine's single public entry point.

    Deliberately does not take `ticker`/`df` parameters the way
    business_quality_engine.compute_business_quality() does — every
    metric in this v1 is computable from the already-resolved `fields`
    dict alone (no metric needs raw ticker.balance_sheet/.financials
    access beyond what the adapter layer already resolved). This is a
    documented, deliberate deviation from SSDS-005's illustrative
    signature, justified by SSDS-006's adapter pattern (which didn't
    exist when SSDS-005 was written) doing that resolution work upfront.

    `fields` must be shaped like services.us_provider_precedence.resolve_field()'s
    own per-field output: {field_name: {"value": ..., "confidence": ..., ...}}.

    Returns an EngineResponse (as a dict, via .to_dict()) per SSDS-005 §6.
    """
    fields = fields or {}

    if sector_bucket in V1_EXCLUDED_SECTOR_BUCKETS:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=0.0,
            explanation=(
                f"Financial Strength v1 does not yet support the {sector_bucket} sector — "
                "confirmed structural data gaps (Sprint #005/#007) require a sector-specific "
                "substitute computation not yet built."
            ),
            metadata={
                "engine": "financial_strength_engine",
                "engine_version": "v1",
                "market": market,
                "sector_bucket": sector_bucket,
                "rejection_reason": "sector_not_yet_supported",
            },
        ).to_dict()

    present = sum(1 for f in MANDATORY_FIELDS if _val(fields, f) is not None)
    data_completeness_pct = round(100 * present / len(MANDATORY_FIELDS), 1)

    if data_completeness_pct < FS.MIN_DATA_COMPLETENESS_PCT:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=data_completeness_pct,
            explanation="Insufficient fundamental data available to assess financial strength.",
            metadata={
                "engine": "financial_strength_engine",
                "engine_version": "v1",
                "market": market,
                "sector_bucket": sector_bucket,
                "data_completeness_pct": data_completeness_pct,
                "rejection_reason": "insufficient_data",
                "missing_mandatory_fields": [f for f in MANDATORY_FIELDS if _val(fields, f) is None],
            },
        ).to_dict()

    liquidity = _liquidity_adequacy(fields)
    leverage = _leverage_and_capital_structure(fields)
    debt_servicing = _debt_servicing_capacity(fields)
    resilience = _balance_sheet_resilience(fields)
    cash_durability = _cash_flow_durability(fields)

    # ── Hard gate — liquidity_distress (SSDS-005 §"Proposed Scoring
    # Categories") — a narrow AND-condition, never triggered by a single
    # weak signal alone.
    current_ratio = liquidity["current_ratio"]
    fcf = _val(fields, "free_cash_flow")
    short_term_debt = _val(fields, "short_term_debt")
    liquidity_distress = (
        current_ratio is not None and current_ratio <= FS.LIQUIDITY_DISTRESS_CURRENT_RATIO_MAX
        and fcf is not None and fcf < 0
        and short_term_debt is not None and short_term_debt > 0
    )
    if liquidity_distress:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=data_completeness_pct,
            risks=[
                f"Current ratio {current_ratio:.2f}x — severe liquidity distress",
                f"Negative free cash flow ({fcf:,.0f}) combined with near-term debt obligations",
            ],
            explanation="Hard liquidity gate failed — current ratio far below 1.0x combined with negative free cash flow and real near-term debt obligations.",
            metadata={
                "engine": "financial_strength_engine",
                "engine_version": "v1",
                "market": market,
                "sector_bucket": sector_bucket,
                "data_completeness_pct": data_completeness_pct,
                "rejection_reason": "liquidity_distress",
                "current_ratio": current_ratio,
                "free_cash_flow": fcf,
            },
        ).to_dict()

    combined = (
        50
        + liquidity["score"]
        + leverage["score"]
        + debt_servicing["score"]
        + resilience["score"]
        + cash_durability["score"]
    )
    score = round(max(0, min(100, combined)))

    if score >= FS.GRADE_STRONG_BUY_MIN:
        grade = Grade.STRONG_BUY
    elif score >= FS.GRADE_BUY_MIN:
        grade = Grade.BUY
    elif score >= FS.GRADE_HOLD_MIN:
        grade = Grade.HOLD
    elif score >= FS.GRADE_WATCH_MIN:
        grade = Grade.WATCH
    else:
        grade = Grade.AVOID

    categories = {
        "Liquidity Adequacy": (liquidity["score"], liquidity["reasons"]),
        "Leverage & Capital Structure": (leverage["score"], leverage["reasons"]),
        "Debt-Servicing Capacity": (debt_servicing["score"], debt_servicing["reasons"]),
        "Balance Sheet Resilience": (resilience["score"], resilience["reasons"]),
        "Cash Flow Durability Under Stress": (cash_durability["score"], cash_durability["reasons"]),
    }
    ranked = sorted(categories.items(), key=lambda kv: kv[1][0], reverse=True)
    strengths = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked if val > 0 and reasons][:3]
    weaknesses = [f"{name}: {reasons[0]}" for name, (val, reasons) in ranked if val < 0 and reasons][-3:]

    risks = []
    if debt_servicing["stress_result"] and not debt_servicing["stress_result"]["passed"]:
        risks.append(debt_servicing["reasons"][-1] if debt_servicing["reasons"] else "Earnings Shock scenario failed")
    if leverage["debt_to_equity_pct"] is not None and leverage["debt_to_equity_pct"] >= FS.DEBT_TO_EQUITY_SEVERE_MIN_PCT:
        risks.append(f"Severe leverage: debt-to-equity {leverage['debt_to_equity_pct']:.0f}%")

    explanation = (
        f"Financial Strength Score {score}/100 ({grade.value}). "
        f"Liquidity Adequacy contributed {liquidity['score']:+.1f}, "
        f"Leverage & Capital Structure {leverage['score']:+.1f}, "
        f"Debt-Servicing Capacity {debt_servicing['score']:+.1f}, "
        f"Balance Sheet Resilience {resilience['score']:+.1f}, "
        f"Cash Flow Durability Under Stress {cash_durability['score']:+.1f}."
    )

    return EngineResponse(
        score=score,
        grade=grade,
        confidence=data_completeness_pct,
        strengths=strengths,
        weaknesses=weaknesses,
        risks=risks,
        explanation=explanation,
        metadata={
            "engine": "financial_strength_engine",
            "engine_version": "v1",
            "market": market,
            "sector_bucket": sector_bucket,
            "data_completeness_pct": data_completeness_pct,
            "current_ratio": current_ratio,
            "cash_ratio": liquidity["cash_ratio"],
            "debt_to_equity_pct": leverage["debt_to_equity_pct"],
            "short_term_debt_share_pct": leverage["short_term_debt_share_pct"],
            "interest_coverage": debt_servicing["interest_coverage"],
            "stress_simulation_results": [debt_servicing["stress_result"]] if debt_servicing["stress_result"] else [],
            "equity_ratio_pct": resilience["equity_ratio_pct"],
            "fcf_margin_pct": cash_durability["fcf_margin_pct"],
            "category_contributions": {
                "liquidity_adequacy": round(liquidity["score"], 1),
                "leverage_capital_structure": round(leverage["score"], 1),
                "debt_servicing_capacity": round(debt_servicing["score"], 1),
                "balance_sheet_resilience": round(resilience["score"], 1),
                "cash_flow_durability_under_stress": round(cash_durability["score"], 1),
            },
        },
    ).to_dict()
