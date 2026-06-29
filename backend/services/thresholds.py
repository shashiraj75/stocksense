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


@dataclass(frozen=True)
class FinancialStrengthThresholds:
    """
    Thresholds for the Financial Strength Intelligence Engine (SSDS-005,
    SSDS-006, implemented Epic 002 Sprint #008 v1). Every value below is
    a new, named constant — none are copied from BusinessQualityThresholds
    even where a similar ratio (e.g. interest coverage) appears in both
    engines, per SES-002 §1's "two genuinely different concepts get
    separate named constants" rule: Business Quality's interest-coverage
    check feeds a quality judgment; this engine's feeds a solvency
    judgment — same ratio, different question, different constant.

    v1 scope (Sprint #008): non-FINANCIAL, non-REAL_ESTATE companies only
    (REAL_ESTATE used as this engine's proxy classification for REITs,
    per Sprint #005/#007's confirmed finding that REITs share much of
    FINANCIAL's structural data gap). Calibrated against a live run of
    76 real US companies (the same universe Sprint #005/#007 validated)
    — see the Sprint #008 Production Readiness Report for the evidence
    behind each value, exactly as SSDS-003's own thresholds were
    calibrated against live data before being trusted (Sprint #004 ->
    #004a cycle).
    """

    # SSDS-005 §5 — minimum fraction of Mandatory fields (the 16-field
    # unified schema, SSDS-006 §5) required before returning a real score
    # rather than REJECTED/insufficient_data. Reuses the same 60% bar
    # BusinessQualityThresholds already established as this codebase's
    # standard "partial data is fine" line — not re-derived, since
    # nothing in this engine's own validation gave a reason to pick a
    # different number.
    MIN_DATA_COMPLETENESS_PCT = 60.0

    # Liquidity Adequacy — Current Ratio (current_assets / current_liabilities).
    # >1.5 is a widely-used "comfortable" liquidity tier in standard
    # credit-analysis practice (current assets cover current liabilities
    # with real margin); <1.0 means current liabilities exceed current
    # assets outright — a real, not just cautionary, liquidity concern.
    CURRENT_RATIO_STRONG_MIN = 1.5
    CURRENT_RATIO_WEAK_MAX = 1.0

    # Liquidity Adequacy — Cash Ratio (cash_and_equivalents / current_liabilities).
    # A stricter liquidity check than the Current Ratio (ignores
    # receivables/inventory entirely) — >0.5 chosen as "cash alone covers
    # half of current obligations," a real margin of safety; <0.2 as a
    # thin-cash-cushion tier.
    CASH_RATIO_STRONG_MIN = 0.5
    CASH_RATIO_THIN_MAX = 0.2

    # Hard gate — liquidity_distress (SSDS-005's own named trigger,
    # distinct from Business Quality's gate). Current Ratio "far below 1"
    # combined with negative free cash flow and real near-term debt
    # obligations (short_term_debt > 0) — calibrated to be a narrow,
    # deliberately rare AND-condition (mirroring SSDS-003 §2's "gate-
    # sprawl is a risk to avoid" instruction), not triggered by ordinary
    # cyclical softness alone.
    LIQUIDITY_DISTRESS_CURRENT_RATIO_MAX = 0.5
    LIQUIDITY_DISTRESS_REQUIRES_NEGATIVE_FCF = True

    # Leverage & Capital Structure — Debt-to-Equity (total_debt / shareholders_equity).
    # A different constant from DebtToEquityThresholds.QUALITY_COMPOUNDER_MAX
    # (50%) even though both concern leverage: that one judges "is this a
    # quality compounder's typical conservative leverage," this one judges
    # "is this leverage level itself a solvency risk" — a structurally
    # higher bar is appropriate for a pure solvency question. 100% (debt
    # equal to equity) as the elevated-risk tier; 200% as the severe tier.
    DEBT_TO_EQUITY_ELEVATED_MIN_PCT = 100.0
    DEBT_TO_EQUITY_SEVERE_MIN_PCT = 200.0

    # Leverage & Capital Structure — short-term debt as a fraction of
    # total debt. >50% means more than half of all debt is due/repriced
    # within the near term — a real refinancing-risk signal independent
    # of the absolute leverage level.
    SHORT_TERM_DEBT_SHARE_ELEVATED_MIN_PCT = 50.0

    # Debt-Servicing Capacity — Interest Coverage (ebit / interest_expense).
    # >3x chosen as comfortably covering interest several times over;
    # <1.5x as a real debt-servicing concern (earnings barely/don't cover
    # interest) — same numeric tiers Business Quality uses for its own,
    # differently-named interest-coverage check (a coincidence of
    # reasonable practitioner convention, not a shared constant; see this
    # dataclass's own docstring on why they're still separate constants).
    INTEREST_COVERAGE_STRONG_MIN = 3.0
    INTEREST_COVERAGE_WEAK_MAX = 1.5

    # Financial Stress Simulation (SSDS-005's named distinctive
    # capability) — Earnings Shock scenario v1: EBIT down 20%, recompute
    # interest coverage. 20% and the "still above 1.0x" pass bar are
    # illustrative starting points carried directly from SSDS-005's own
    # placeholder framing — calibrated no further than that in this
    # sprint, since shock-magnitude calibration against real sector
    # volatility data is explicitly named in SSDS-005 as future work, not
    # this implementation sprint's job.
    STRESS_EARNINGS_SHOCK_PCT = 20.0
    STRESS_INTEREST_COVERAGE_PASS_MIN = 1.0

    # Balance Sheet Resilience — Equity Ratio (shareholders_equity / total_assets).
    # >40% chosen as a real equity cushion (less than 60% of the balance
    # sheet is debt-or-liability-funded); <15% as a thin-cushion tier.
    EQUITY_RATIO_STRONG_MIN_PCT = 40.0
    EQUITY_RATIO_THIN_MAX_PCT = 15.0

    # Cash Flow Durability Under Stress — Free Cash Flow Margin (free_cash_flow / revenue).
    # >10% chosen as a healthy, self-funding margin; negative FCF margin
    # as the clear concern tier (the business consumes cash rather than
    # generating it, before any stress is even applied).
    FCF_MARGIN_STRONG_MIN_PCT = 10.0
    FCF_MARGIN_NEGATIVE_MAX_PCT = 0.0

    # SSDS-003 §6-style grade bands, same base-50-plus-capped-buckets
    # convention as Business Quality, reused for consistency rather than
    # inventing a second grading scale.
    GRADE_STRONG_BUY_MIN = 80
    GRADE_BUY_MIN = 65
    GRADE_HOLD_MIN = 50
    GRADE_WATCH_MIN = 35

    # Prediction Engine integration (Epic 002 Sprint #010) — confidence-
    # only adjustment caps, mirroring the existing _apply_risk_reward_
    # adjustment/_apply_pledge_adjustment pattern exactly (both operate
    # on confidence only, never the composite score/signal, so Daily
    # Picks ranking and the IC learning engine stay unaffected). ±6 is
    # deliberately small relative to the existing factors' typical
    # swings (the quality-factors blend alone can move composite_score
    # by up to ±10, before risk-penalty/regime/global-macro adjustments
    # on top of that) — chosen so this signal is additive, not
    # dominant, per this sprint's explicit rule.
    PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP = 6.0

    # Mirrors the existing severe-risk convention exactly
    # (_apply_risk_reward_adjustment/_apply_pledge_adjustment both cap
    # confidence at 30 for a confirmed red flag) — reused, not
    # reinvented, for the liquidity_distress hard-gate's influence on
    # the Prediction Engine's output.
    PREDICTION_ENGINE_LIQUIDITY_DISTRESS_CONFIDENCE_CAP = 30


@dataclass(frozen=True)
class GrowthIntelligenceThresholds:
    """Thresholds for the Growth Intelligence Engine v1 (SSDS-007, Epic 003
    Sprint #003). Deliberately a SEPARATE registry entry from the
    pre-existing GrowthThresholds/GROWTH above (owned by the Multibagger
    scorecard and the prediction-engine quality gate's turnaround
    exception) — SSDS-007's own Open Question #1 named this as undecided;
    this sprint resolves it by NOT reusing or renaming GROWTH, per
    SES-002 §1's "two genuinely different concepts get separate named
    constants" even where the underlying ratio (e.g. sales growth %)
    looks similar. Every value below is a first-pass, uncalibrated
    estimate (no backtested/outcome-validated calibration exists yet for
    this brand-new engine) — named explicitly as a Known Limitation in
    the v1 implementation report, not presented as production-calibrated."""

    MIN_CORE_FIELDS_PRESENT = 2  # of 4 core fields (revenue/profit growth, eps_trend, durability series) — below this, REJECTED
    MIN_DATA_COMPLETENESS_PCT = 60.0  # over all 7 possible fields — drives the `confidence` output, not the reject gate

    GRADE_STRONG_BUY_MIN = 80
    GRADE_BUY_MIN = 65
    GRADE_HOLD_MIN = 50
    GRADE_WATCH_MIN = 35

    # Calibration Sprint #004 finding: a marginal ±3 EPS Trend
    # "mixed_positive"/"mixed_negative" contribution was qualifying as a
    # headline strength/weakness for companies whose every other category
    # scored on the opposite side — a real, found misleading-statement
    # defect (e.g. COALINDIA/SRF, both 6/100 avoid, listing "EPS Trend:
    # mixed_positive" as their only "strength"). 5.0 excludes only that
    # ±3 EPS-trend tier while still surfacing every other category's
    # ±8-and-up signal — a presentation filter, not a scoring-weight change.
    MIN_NOTABLE_CONTRIBUTION = 5.0

    # Revenue/Profit Growth (±15 base, ±18 with the acceleration bonus) —
    # 15% chosen as a round, moderately high bar consistent with the
    # pre-existing GROWTH registry's own
    # SALES_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT=12.0 (a different,
    # independently-calibrated consumer's bar) without copying its value.
    # The acceleration cap is intentionally higher than the base cap —
    # caught during unit testing (test_growth_acceleration_bonus_applied):
    # clamping the bonus to the same ±15 ceiling as the base score made
    # the bonus dead code for every company already at or above the
    # strong threshold, which is the most common case it's meant to
    # apply to. A real, fixed defect, not a design choice.
    REVENUE_GROWTH_STRONG_MIN_PCT = 15.0
    REVENUE_GROWTH_WEAK_MAX_PCT = 0.0
    REVENUE_GROWTH_ACCELERATION_BONUS = 3.0
    REVENUE_GROWTH_ACCELERATION_CAP = 18.0
    PROFIT_GROWTH_STRONG_MIN_PCT = 15.0
    PROFIT_GROWTH_WEAK_MAX_PCT = 0.0
    PROFIT_GROWTH_ACCELERATION_BONUS = 3.0
    PROFIT_GROWTH_ACCELERATION_CAP = 18.0

    # EPS Trend (±8) — categorical, smaller cap than the numeric
    # Revenue/Profit Growth categories since it's a coarser 4-bucket signal.
    EPS_TREND_ACCELERATING_SCORE = 8.0
    EPS_TREND_MIXED_POSITIVE_SCORE = 3.0
    EPS_TREND_MIXED_NEGATIVE_SCORE = -3.0
    EPS_TREND_DECELERATING_SCORE = -8.0

    # Growth Durability (±12) — coefficient of variation of YoY growth
    # rates computed from the revenue history series. Below LOW_CV: a
    # consistent, low-volatility trend. Above HIGH_CV: an erratic one.
    DURABILITY_LOW_CV = 0.25
    DURABILITY_HIGH_CV = 0.75
    DURABILITY_STRONG_SCORE = 12.0
    DURABILITY_WEAK_SCORE = -12.0

    # Operating Profit Growth (±12) — same bar shape as Revenue/Profit
    # Growth, independently named (not reused) since this is a distinct
    # metric per SSDS-007's Metric Catalogue.
    OPERATING_PROFIT_GROWTH_STRONG_MIN_PCT = 15.0
    OPERATING_PROFIT_GROWTH_WEAK_MAX_PCT = 0.0
    OPERATING_PROFIT_GROWTH_STRONG_SCORE = 12.0
    OPERATING_PROFIT_GROWTH_WEAK_SCORE = -12.0

    # Reinvestment Efficiency (±8) — ratio of operating-profit growth to
    # invested-capital growth. >=1.2x: profit growing meaningfully faster
    # than the capital base funding it (efficient). <=0.5x: capital
    # growing faster than the profit it's producing (inefficient).
    REINVESTMENT_EFFICIENCY_STRONG_MIN_RATIO = 1.2
    REINVESTMENT_EFFICIENCY_WEAK_MAX_RATIO = 0.5
    REINVESTMENT_EFFICIENCY_STRONG_SCORE = 8.0
    REINVESTMENT_EFFICIENCY_WEAK_SCORE = -8.0

    # Margin Trend (±8) — percentage-point change in operating margin
    # across the available history window.
    MARGIN_EXPANSION_STRONG_MIN_PCT = 2.0
    MARGIN_CONTRACTION_WEAK_MAX_PCT = -2.0
    MARGIN_TREND_STRONG_SCORE = 8.0
    MARGIN_TREND_WEAK_SCORE = -8.0

    # Prediction Engine integration (Epic 003 Sprint #006's decision,
    # implemented Sprint #007) — deliberately smaller than
    # FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP's own
    # ±6, proportionate to weaker, earlier-stage evidence: Growth
    # Intelligence's Sprint #005 outcome-correlation result for India was
    # real and positive but modest in magnitude (Spearman rho +0.15 to
    # +0.17, not a strong correlation), and is the first time any engine
    # in this codebase has had its forward-outcome relationship measured
    # at all. India-only by Sprint #006's explicit decision — applied
    # only inside _apply_growth_intelligence_adjustment's market=="IN"
    # branch, never for US.
    PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP = 3.0


@dataclass(frozen=True)
class ValuationIntelligenceThresholds:
    """Thresholds for the Valuation Intelligence Engine v1 (SSDS-008,
    Epic 004 Sprint #003). Deliberately a SEPARATE registry entry from the
    pre-existing ValuationThresholds/VALUATION above (owned by the
    Multibagger scorecard's PE_QUALITY_COMPOUNDER_MAX/
    EV_EBITDA_QUALITY_COMPOUNDER_MAX checklist) — SSDS-008's Evidence
    Checkpoint named this exact naming collision and resolved it the same
    way SSDS-007 resolved GROWTH/GROWTH_INTELLIGENCE: a new name, never a
    reuse. Every band below is a standard, widely-cited equity-valuation
    convention (Graham's P/E<15, Damodaran-style EV/EBITDA bands, etc.) —
    chosen because this sprint's brief explicitly excludes "threshold
    optimisation"/calibration; these are reasonable v1 starting points,
    not backtested or outcome-calibrated, named as a Known Limitation in
    the v1 implementation report, mirroring exactly how
    GrowthIntelligenceThresholds entered production uncalibrated."""

    MIN_CORE_FIELDS_PRESENT = 2  # of 4 core fields — below this, REJECTED
    MIN_DATA_COMPLETENESS_PCT = 60.0

    GRADE_STRONG_BUY_MIN = 80  # heavily undervalued
    GRADE_BUY_MIN = 65
    GRADE_HOLD_MIN = 50
    GRADE_WATCH_MIN = 35  # below this: AVOID — richly valued

    MIN_NOTABLE_CONTRIBUTION = 5.0

    # Earnings Multiple (±15) — Graham's classic <15 "cheap" anchor;
    # >30 is the conventional "richly valued" line cited across general
    # equity-research practice (not specific to any single methodology).
    PE_CHEAP_MAX = 15.0
    PE_EXPENSIVE_MIN = 30.0
    EARNINGS_MULTIPLE_STRONG_SCORE = 15.0
    EARNINGS_MULTIPLE_WEAK_SCORE = -15.0

    # EV/Sales (±12) — capital-structure-neutral; <1.5 and >5 are
    # conventional general-market bands (richer multiples are normal and
    # expected for high-growth/SaaS-style businesses specifically, which
    # is exactly why this is a secondary signal, not the primary one).
    EV_SALES_CHEAP_MAX = 1.5
    EV_SALES_EXPENSIVE_MIN = 5.0
    EV_SALES_STRONG_SCORE = 12.0
    EV_SALES_WEAK_SCORE = -12.0

    # Price/Book (±10) — sector-gated to FINANCIAL/REAL_ESTATE only, per
    # SSDS-008's Design Philosophy ("no single-ratio reduction" + the
    # Research Report's own asset-based-valuation sector-gating rationale).
    # <1 is the classic Graham net-net-adjacent anchor; >3 is the
    # conventional bank-analyst "expensive" line.
    PRICE_BOOK_CHEAP_MAX = 1.0
    PRICE_BOOK_EXPENSIVE_MIN = 3.0
    PRICE_BOOK_STRONG_SCORE = 10.0
    PRICE_BOOK_WEAK_SCORE = -10.0

    # EV/EBITDA (±12) — population-gated to non-FINANCIAL companies per
    # Sprint #002's live evidence (Banks/NBFC structurally lack
    # EBITDA-shaped income statements: 0/10 Banks, 1/9 NBFC in the
    # 113-company sample). <8 and >15 are standard Damodaran-style bands.
    EV_EBITDA_CHEAP_MAX = 8.0
    EV_EBITDA_EXPENSIVE_MIN = 15.0
    EV_EBITDA_STRONG_SCORE = 12.0
    EV_EBITDA_WEAK_SCORE = -12.0

    # Dividend Income (±10 yield, ±5 sustainability modifier) — a high
    # yield is rewarded; absence of a dividend is NOT penalized (many
    # legitimate growth companies pay none), only a genuinely unsustainable
    # payout ratio on top of a yield is. 3% is a commonly cited "attractive
    # income" threshold; 60%/90% payout-ratio bands are standard
    # sustainability conventions (Research Report §5).
    DIVIDEND_YIELD_ATTRACTIVE_MIN_PCT = 3.0
    DIVIDEND_YIELD_STRONG_SCORE = 10.0
    PAYOUT_RATIO_SUSTAINABLE_MAX = 0.60
    PAYOUT_RATIO_RISKY_MIN = 0.90
    DIVIDEND_SUSTAINABILITY_BONUS = 5.0
    DIVIDEND_SUSTAINABILITY_PENALTY = -5.0

    # Free Cash Flow Yield (±10) — population-gated the same way as
    # EV/EBITDA (inherits Growth Intelligence's own confirmed FCF-
    # approximation imprecision for India). >=8% attractive, <2% weak —
    # conventional FCF-yield bands.
    FCF_YIELD_ATTRACTIVE_MIN_PCT = 8.0
    FCF_YIELD_WEAK_MAX_PCT = 2.0
    FCF_YIELD_STRONG_SCORE = 10.0
    FCF_YIELD_WEAK_SCORE = -10.0

    # PEG Ratio (±8) — population-gated (inherits EV/EBITDA's gate) and
    # additionally depends on Growth Intelligence's own validated growth
    # output (SSDS-008's "read, don't recompute" rule) or, where that is
    # unavailable, the provider's own pre-computed field (confirmed only
    # 3.5% available for India, ~reliable for US). <1 and >2 are Peter
    # Lynch's own commonly cited PEG conventions.
    PEG_CHEAP_MAX = 1.0
    PEG_EXPENSIVE_MIN = 2.0
    PEG_STRONG_SCORE = 8.0
    PEG_WEAK_SCORE = -8.0

    # Prediction Engine integration (Epic 004 Sprint #006's decision,
    # implemented Sprint #007) — ASYMMETRIC, the first such cap in this
    # codebase, directly reflecting Sprint #006's Standalone Consumption
    # Rule: undervaluation may only ever apply a small, cross-engine-
    # gated boost (smaller than Growth Intelligence India's own +3,
    # proportionate to this engine's uniquely severe demonstrated
    # downside — Sprint #005's RELINFRA case: a 73/100 score followed by
    # a real, measured -82.0% return); overvaluation may apply a larger,
    # UNGATED demotion, since "warn against an expensive one" carries
    # none of the boost side's promotion risk (Sprint #004/#005 found
    # zero cases of a confirmed-expensive premium compounder being
    # wrongly penalized).
    PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_POSITIVE = 2.0
    PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_NEGATIVE = 4.0


# Singleton instances — import these, not the dataclasses, from call sites.
DEBT_TO_EQUITY = DebtToEquityThresholds()
PROFITABILITY = ProfitabilityThresholds()
CASH_FLOW = CashFlowThresholds()
GROWTH = GrowthThresholds()
VALUATION = ValuationThresholds()
GOVERNANCE = GovernanceThresholds()
RISK_PENALTY = RiskPenaltyThresholds()
FINANCIAL_STRENGTH = FinancialStrengthThresholds()
BUSINESS_QUALITY = BusinessQualityThresholds()
GROWTH_INTELLIGENCE = GrowthIntelligenceThresholds()
VALUATION_INTELLIGENCE = ValuationIntelligenceThresholds()
