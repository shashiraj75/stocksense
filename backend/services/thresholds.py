"""
Central threshold registry for the Selection Engine.

SEAR-001 (Documentation/Engineering-Handbook/Architecture/Sprint-001-Selection-Engine-Audit.md)
found the same financial concepts (debt-to-equity, ROE, ROCE, valuation) judged
against different hardcoded numbers in different files with no shared source of
truth. This module is that source of truth for the Sprint #002 migration.

Scope note: this sprint migrates the *gate-relevant* thresholds named in the
audit (the ones controlling hard-reject / red-flag / risk-penalty decisions).
The granular per-bucket scoring curves in `prediction_engine.py`'s
`_fundamental_score` (e.g. the ROE 0.20/0.10 score-bucket cutoffs) are a much
larger, continuous scoring surface rather than a small set of named gates —
migrating those is left to a future sprint per the "keep changes incremental"
rule, and is tracked as a Sprint 003+ follow-up rather than done here.

Every threshold below states which file/function it replaced and why that
specific value was chosen, where the original code documented a reason.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DebtToEquityThresholds:
    """All cutoffs are in percent (yfinance/screener.in convention: 100 = 1.0x)."""

    # multibagger_scorecard.py:44 — checklist "Debt/Equity < 50%"
    # multibagger_scorecard.py:102 — elite_strong_buy hard requirement
    QUALITY_COMPOUNDER_MAX = 50.0

    # prediction_engine.py:1067 — fundamental-score balance-sheet bucket, "low debt" bonus
    LOW_DEBT_BONUS_MAX = 50.0

    # prediction_engine.py:1378 — quality-gate turnaround-exception "contained leverage" check
    TURNAROUND_EXCEPTION_MAX = 150.0

    # prediction_engine.py:1064 — fundamental-score balance-sheet bucket, "elevated" penalty
    # multibagger_scorecard.py:76 — Anti-Loss red-flag "high leverage"
    ELEVATED_PENALTY_MIN = 150.0

    # prediction_engine.py:199 — risk-penalty "elevated leverage" tier
    RISK_PENALTY_ELEVATED_MIN = 200.0

    # prediction_engine.py:196 — risk-penalty "high debt" tier (heavier penalty)
    # prediction_engine.py:1061 — fundamental-score "very high" penalty tier
    RISK_PENALTY_SEVERE_MIN = 300.0

    # prediction_engine.py:1388 — hard quality-gate rejection (non-financial sector only)
    HARD_REJECT_MIN = 500.0


@dataclass(frozen=True)
class ProfitabilityThresholds:
    """ROE/ROCE thresholds. Fractional convention (0.18) unless noted as _PCT (18)."""

    # prediction_engine.py:351 (risk penalty), 1351 (quality gate) — severely negative ROE
    ROE_SEVERE_NEGATIVE = -0.10

    # prediction_engine.py:220 — risk-penalty "negative ROE, destroying value" tier
    ROE_NEGATIVE_RISK_PENALTY = -0.05

    # prediction_engine.py:1353 — quality-gate deep-loss profit-margin fallback
    PROFIT_MARGIN_SEVERE_NEGATIVE = -0.15

    # multibagger_scorecard.py:38 — checklist "ROCE > 15%"
    # multibagger_scorecard.py:101 — elite_strong_buy hard requirement
    ROCE_QUALITY_COMPOUNDER_MIN_PCT = 15.0

    # prediction_engine.py:1379 — quality-gate turnaround-exception "decent capital efficiency"
    # Deliberately lower than the 15% Quality Compounder bar — this exception is for
    # businesses earning back to health, not already-proven compounders.
    ROCE_TURNAROUND_EXCEPTION_MIN = 0.08

    # multibagger_scorecard.py:37 — checklist "ROE > 18%"
    ROE_QUALITY_COMPOUNDER_MIN_PCT = 18.0


@dataclass(frozen=True)
class CashFlowThresholds:
    # multibagger_scorecard.py:46 — checklist "Operating cash flow positive"
    # multibagger_scorecard.py:75 — Anti-Loss red-flag "negative OCF"
    # prediction_engine.py:1075 — fundamental-score balance-sheet bucket OCF check
    # prediction_engine.py:1360 — quality-gate OCF check (medium/long horizons only)
    OCF_MUST_BE_POSITIVE = 0.0

    # prediction_engine.py:1082 — fundamental-score balance-sheet bucket, 3Y OCF growth bonus
    OCF_GROWTH_STRONG_MIN_PCT = 30.0


@dataclass(frozen=True)
class GrowthThresholds:
    # prediction_engine.py:1377 — quality-gate turnaround-exception "strong growth" requirement
    REVENUE_GROWTH_TURNAROUND_EXCEPTION_MIN = 0.15

    # multibagger_scorecard.py:41 — checklist "Sales growth > 12% (3Y)"
    SALES_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT = 12.0

    # multibagger_scorecard.py:104 — elite_strong_buy hard requirement
    SALES_GROWTH_3Y_ELITE_MIN_PCT = 10.0

    # multibagger_scorecard.py:42 — checklist "Profit growth > 12% (3Y)"
    PROFIT_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT = 12.0


@dataclass(frozen=True)
class ValuationThresholds:
    # multibagger_scorecard.py:48 — checklist "P/E < 35".
    # SEAR-001 flagged this as proven over-tight (Pidilite/Asian Paints/Havells/
    # Nestlé all failed ONLY this check on live data) and structurally redundant
    # with the SQL screen filter. NOT changed in this sprint — Sprint #002 is
    # engineering infrastructure only, not a business-logic change; recalibration
    # is explicitly scoped to Sprint 004 (roadmap item 2.2) with stakeholder review.
    PE_QUALITY_COMPOUNDER_MAX = 35.0

    # multibagger_scorecard.py:49 — checklist "EV/EBITDA < 20"
    EV_EBITDA_QUALITY_COMPOUNDER_MAX = 20.0


@dataclass(frozen=True)
class GovernanceThresholds:
    # multibagger_scorecard.py:58 — checklist "No promoter pledge (latest)"
    PROMOTER_PLEDGE_CLEAN_MAX_PCT = 1.0

    # multibagger_scorecard.py:74 — Anti-Loss red-flag "promoter pledge"
    PROMOTER_PLEDGE_RED_FLAG_MIN_PCT = 5.0

    # multibagger_scorecard.py:45 — checklist "Interest Coverage > 3x"
    INTEREST_COVERAGE_MIN = 3.0


@dataclass(frozen=True)
class RiskPenaltyThresholds:
    # prediction_engine.py:205/208 — beta risk-penalty tiers
    BETA_HIGH = 2.0
    BETA_ABOVE_AVERAGE = 1.6

    # prediction_engine.py:228/231 — risk_management sub-score penalty tiers (0-100 scale)
    RISK_SUBSCORE_POOR_MAX = 35
    RISK_SUBSCORE_BELOW_AVERAGE_MAX = 45


@dataclass(frozen=True)
class BusinessQualityThresholds:
    """
    New thresholds introduced for the Business Quality Engine (SSDS-003,
    Sprint #004). Every value below is justified in its own comment —
    none are copied from an existing call site, since this is new scoring
    surface, not a migration of existing literals.
    """

    # SSDS-003 §2 — combined-score grade bands, mirroring the existing
    # base-50-plus-capped-buckets convention used elsewhere (e.g.
    # prediction_engine.py's _fundamental_score). Bands chosen to leave a
    # genuinely narrow "Exceptional" tier (most businesses should NOT
    # qualify) rather than grade-inflating the top band.
    GRADE_STRONG_BUY_MIN = 80
    GRADE_BUY_MIN = 65
    GRADE_HOLD_MIN = 50
    GRADE_WATCH_MIN = 35
    # Below GRADE_WATCH_MIN = "avoid" tier (not a separate constant — it's
    # simply "everything else", same convention as multibagger_scorecard.py's
    # verdict fallthrough).

    # SSDS-003 §5 — minimum fraction of Mandatory metrics (per the sector's
    # applicability table, not the universal list) required to return a
    # real score rather than REJECTED/insufficient_data. 60% chosen as the
    # same bar already used implicitly elsewhere in this codebase's
    # "partial data is fine — use whatever we extracted" pattern, made
    # explicit and numeric here per SSDS-003's instruction not to leave
    # this rule undocumented.
    MIN_DATA_COMPLETENESS_PCT = 60.0

    # SSDS-003 §3 — new Cash Conversion Ratio (OCF / Net Income) tiers.
    # >0.8 chosen as "most reported profit is converting to cash" (a
    # business converting LESS than 80% of profit to cash for a sustained
    # period is increasingly relying on accruals); <0.5 chosen as a clear
    # red flag tier (less than half of reported profit is cash).
    CASH_CONVERSION_STRONG_MIN = 0.8
    CASH_CONVERSION_WEAK_MAX = 0.5

    # SSDS-003 §3 — Sloan (1996) accruals-ratio "aggressive" tier, used by
    # the hard-gate AND-condition in SSDS-003 §2 (combined with Altman
    # distress). Sloan's own research ranks accruals into deciles and finds
    # the highest-accruals decile underperforms; 10% is a commonly cited
    # rule-of-thumb cutoff for "high accruals" in practitioner use of the
    # ratio, used here as a deliberate, named simplification of the full
    # decile-ranking methodology (which requires a cross-sectional universe
    # ranking this engine does not have access to at single-stock scoring
    # time) — documented as a Known Limitation in SSDS-003, not silently
    # assumed equivalent.
    ACCRUALS_AGGRESSIVE_MIN_PCT = 10.0

    # SSDS-003 §3 — Beneish (1999) M-Score manipulation-likelihood
    # threshold. -1.78 is the original paper's published cutoff — not a
    # StockSense360-specific choice, reused as-is rather than re-derived.
    BENEISH_MANIPULATION_LIKELY_MIN = -1.78

    # Calibration fix (Business Quality Engine Production Readiness
    # Validation, Architecture/Business-Quality-Engine-Production-
    # Readiness-Validation.md, Phase 6 Finding B / Phase 9 Recommendation
    # B2). Confirmed with live data: the reused Piotroski F-Score
    # (quality_metrics_score) has no sector-awareness internally, and
    # several of its 9 sub-checks (declining leverage, improving asset
    # turnover, improving gross margin) are structurally inapplicable —
    # in some cases backwards — for a balance-sheet-driven business model.
    # Evidence: YESBANK (Piotroski 7/9) scored identically to HDFCBANK/
    # ICICIBANK, while BAJAJFINSV/BAJFINANCE (Piotroski 3/9) scored the
    # LOWEST of 46 real companies tested, despite being widely-regarded
    # financial compounders. 0.5 is a deliberate half-weight discount, not
    # a full exemption (the existing FINANCIAL exemptions for D/E and
    # interest coverage already remove the checks that don't apply at
    # all; Piotroski still carries SOME signal for a bank — declining ROA,
    # cash-vs-accrual-earnings checks remain meaningful — so it is
    # discounted, not zeroed).
    PIOTROSKI_FINANCIAL_SECTOR_WEIGHT = 0.5


# Singleton instances — import these, not the dataclasses, from call sites.
DEBT_TO_EQUITY = DebtToEquityThresholds()
PROFITABILITY = ProfitabilityThresholds()
CASH_FLOW = CashFlowThresholds()
GROWTH = GrowthThresholds()
VALUATION = ValuationThresholds()
GOVERNANCE = GovernanceThresholds()
RISK_PENALTY = RiskPenaltyThresholds()
BUSINESS_QUALITY = BusinessQualityThresholds()
