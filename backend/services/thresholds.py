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


# Singleton instances — import these, not the dataclasses, from call sites.
DEBT_TO_EQUITY = DebtToEquityThresholds()
PROFITABILITY = ProfitabilityThresholds()
CASH_FLOW = CashFlowThresholds()
GROWTH = GrowthThresholds()
VALUATION = ValuationThresholds()
GOVERNANCE = GovernanceThresholds()
RISK_PENALTY = RiskPenaltyThresholds()
