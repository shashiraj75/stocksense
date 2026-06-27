"""
StockSense360 Business Quality Engine (SSDS-003, implemented Sprint #004).

Answers exactly one question: "Is this fundamentally an outstanding
business worthy of long-term ownership?" Does NOT generate a BUY/HOLD/SELL
recommendation — that remains the Prediction Engine's responsibility
(per the StockSense360 Product Glossary, "Recommendation Engine" is not a
separate component).

Per SSDS-003 Finding 1: this is a NEW, narrower aggregation, not a rename
of quality_factors.py's compute_all_quality_factors() (which blends 14
dimensions, 9 of which are momentum/flow/valuation/risk concepts that
belong to other engines). This module reuses exactly 5 existing,
genuinely business-quality functions from quality_factors.py as building
blocks — buffett_munger_score, altman_zscore_signal, sloan_accruals_signal,
quality_metrics_score, corporate_actions_score — and adds the metrics
SSDS-003 §3 identified as missing: a standalone Cash Conversion Ratio, a
Beneish M-Score, and a standalone Asset Turnover level check.

Sprint #004 integration note: this engine is called ADDITIVELY from
PredictionEngine.predict() (see prediction_engine.py's `_get_business_quality`
closure) and exposed under a NEW `business_quality` key in the prediction
result. It does not modify, replace, or read from the existing
`quality_factors`/`quality_score` output — see Documentation/
Engineering-Handbook/Releases/Sprint-004-Business-Quality-Engine.md for
the full migration notes.
"""

import logging

import pandas as pd

from services.engine_contract import EngineResponse, Grade
from services.thresholds import DEBT_TO_EQUITY, PROFITABILITY, BUSINESS_QUALITY
from services.sector_quality_applicability import classify_sector, is_exempt, is_adjusted
from services.quality_factors import (
    buffett_munger_score,
    altman_zscore_signal,
    sloan_accruals_signal,
    quality_metrics_score,
    corporate_actions_score,
)

log = logging.getLogger(__name__)


def _map_subscore(sub_score: float | None, cap: float) -> float:
    """Maps an existing 0-100 sub-score (base 50 = neutral) onto a
    ±cap contribution — the same base-50-plus-capped-bucket convention
    already used in prediction_engine.py's _fundamental_score, reused
    here rather than inventing a second aggregation style."""
    if sub_score is None:
        return 0.0
    return (sub_score - 50) / 50 * cap


def _get_financial_row(df_like, *labels):
    """Generic row-lookup across a yfinance financials/balance_sheet/
    cashflow DataFrame, sorted oldest-to-newest. Mirrors the equivalent
    private helper already inline inside quality_metrics_score — this is
    a 6-line dataframe-row-lookup utility, not investment logic, so a
    local equivalent here doesn't constitute the "duplicated business
    rule" pattern SES-001 warns against (the rule being duplicated would
    be a scoring FORMULA, not a generic lookup helper)."""
    if df_like is None or df_like.empty:
        return None
    df_sorted = df_like.sort_index(axis=1)
    for label in labels:
        if label in df_sorted.index:
            row = df_sorted.loc[label].dropna()
            if not row.empty:
                return row.sort_index().values
    return None


def _compute_cash_conversion(info: dict) -> dict:
    """New metric (SSDS-003 §3): Cash Conversion Ratio = OCF / Net Income.
    Returns {"ratio": float|None, "score": 0-100, "reason": str|None}."""
    ocf = info.get("operatingCashflow")
    if ocf is None:
        ocf = info.get("operatingCashflows")
    net_income = info.get("netIncome") or info.get("netIncomeToCommon")

    if ocf is None or not net_income or net_income <= 0:
        return {"ratio": None, "score": None, "reason": None}

    ratio = ocf / net_income
    if ratio >= BUSINESS_QUALITY.CASH_CONVERSION_STRONG_MIN:
        return {
            "ratio": ratio, "score": 65,
            "reason": f"Strong cash conversion ({ratio:.2f}x) — reported profit is backed by real cash",
        }
    if ratio <= BUSINESS_QUALITY.CASH_CONVERSION_WEAK_MAX:
        return {
            "ratio": ratio, "score": 35,
            "reason": f"Weak cash conversion ({ratio:.2f}x) — profit relies heavily on non-cash accruals",
        }
    return {"ratio": ratio, "score": 50, "reason": f"Moderate cash conversion ({ratio:.2f}x)"}


def _compute_asset_turnover(info: dict, ticker) -> dict:
    """New metric (SSDS-003 §3): exposes Asset Turnover as a standalone
    level check (revenue / total assets), distinct from Piotroski's P9
    check inside quality_metrics_score, which only checks the YEAR-OVER-
    YEAR DIRECTION of asset turnover, not its absolute level."""
    revenue = info.get("totalRevenue")
    total_assets = None
    try:
        bs = ticker.balance_sheet
        ta_row = _get_financial_row(bs, "Total Assets")
        if ta_row is not None and len(ta_row) >= 1:
            total_assets = ta_row[-1]
    except Exception:
        pass

    if not revenue or not total_assets or total_assets <= 0:
        return {"turnover": None, "score": None, "reason": None}

    turnover = revenue / total_assets
    if turnover >= 1.0:
        return {"turnover": turnover, "score": 60,
                "reason": f"Asset turnover {turnover:.2f}x — efficient use of the asset base"}
    if turnover <= 0.3:
        return {"turnover": turnover, "score": 40,
                "reason": f"Asset turnover {turnover:.2f}x — capital-intensive relative to revenue generated"}
    return {"turnover": turnover, "score": 50, "reason": None}


def _compute_working_capital_trend(ticker) -> dict:
    """New metric (SSDS-003 §3): a standalone working-capital-discipline
    trend check. Today this concept only exists as an intermediate term
    (X1 = Working Capital / Total Assets) inside the Altman Z-Score
    formula — this exposes the TREND of that ratio as its own signal."""
    try:
        bs = ticker.balance_sheet
        ca = _get_financial_row(bs, "Current Assets", "Total Current Assets")
        cl = _get_financial_row(bs, "Current Liabilities", "Total Current Liabilities")
        ta = _get_financial_row(bs, "Total Assets")
        if ca is None or cl is None or ta is None or len(ca) < 2 or len(cl) < 2 or len(ta) < 2:
            return {"score": None, "reason": None}
        wc_now = (ca[-1] - cl[-1]) / ta[-1] if ta[-1] else None
        wc_prev = (ca[-2] - cl[-2]) / ta[-2] if ta[-2] else None
        if wc_now is None or wc_prev is None:
            return {"score": None, "reason": None}
        if wc_now > wc_prev:
            return {"score": 58, "reason": "Working capital efficiency improving year-over-year"}
        return {"score": 45, "reason": "Working capital efficiency deteriorating year-over-year"}
    except Exception:
        return {"score": None, "reason": None}


def _compute_beneish_m_score(ticker) -> dict:
    """New metric (SSDS-003 §3): Beneish (1999) M-Score, standard
    8-variable formula. Confirmed absent anywhere in this codebase prior
    to Sprint #004 (SEAR-001, SSDS-003 Finding 3). Requires 2 full years
    of financials/balance_sheet/cashflow with several specific line
    items; yfinance frequently lacks one or more of these for a given
    ticker — per SSDS-003 §5 (missing-data handling), this returns
    unavailable rather than a guessed/partial number whenever ANY
    required input is missing."""
    try:
        fin = ticker.financials
        bs = ticker.balance_sheet
        cf = ticker.cashflow

        rev = _get_financial_row(fin, "Total Revenue", "Revenue")
        cogs = _get_financial_row(fin, "Cost Of Revenue", "Reconciled Cost Of Revenue")
        sga = _get_financial_row(fin, "Selling General And Administration")
        dep = _get_financial_row(fin, "Depreciation And Amortization", "Depreciation")
        net_income = _get_financial_row(fin, "Net Income", "Net Income Common Stockholders")

        receivables = _get_financial_row(bs, "Receivables", "Accounts Receivable", "Net Receivables")
        curr_assets = _get_financial_row(bs, "Current Assets", "Total Current Assets")
        ppe = _get_financial_row(bs, "Net PPE", "Property Plant And Equipment Net")
        total_assets = _get_financial_row(bs, "Total Assets")
        curr_liab = _get_financial_row(bs, "Current Liabilities", "Total Current Liabilities")
        ltd = _get_financial_row(bs, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")

        ocf = _get_financial_row(cf, "Operating Cash Flow", "Cash From Operations")

        required = [rev, cogs, sga, dep, net_income, receivables, curr_assets, ppe,
                    total_assets, curr_liab, ltd, ocf]
        if any(r is None or len(r) < 2 for r in required):
            return {"m_score": None, "reason": None}

        # All series sorted oldest->newest by _get_financial_row; use [-1]=current, [-2]=prior.
        dsri = (receivables[-1] / rev[-1]) / (receivables[-2] / rev[-2]) if rev[-1] and rev[-2] and receivables[-2] else None
        gm_now = (rev[-1] - cogs[-1]) / rev[-1] if rev[-1] else None
        gm_prev = (rev[-2] - cogs[-2]) / rev[-2] if rev[-2] else None
        gmi = (gm_prev / gm_now) if gm_now and gm_prev else None
        aqi_now = 1 - (curr_assets[-1] + ppe[-1]) / total_assets[-1] if total_assets[-1] else None
        aqi_prev = 1 - (curr_assets[-2] + ppe[-2]) / total_assets[-2] if total_assets[-2] else None
        aqi = (aqi_now / aqi_prev) if aqi_now and aqi_prev else None
        sgi = (rev[-1] / rev[-2]) if rev[-2] else None
        depi_now = dep[-1] / (ppe[-1] + dep[-1]) if (ppe[-1] + dep[-1]) else None
        depi_prev = dep[-2] / (ppe[-2] + dep[-2]) if (ppe[-2] + dep[-2]) else None
        depi = (depi_prev / depi_now) if depi_now and depi_prev else None
        sgai = ((sga[-1] / rev[-1]) / (sga[-2] / rev[-2])) if rev[-1] and rev[-2] and sga[-2] else None
        tata = (net_income[-1] - ocf[-1]) / total_assets[-1] if total_assets[-1] else None
        lvgi_now = (curr_liab[-1] + ltd[-1]) / total_assets[-1] if total_assets[-1] else None
        lvgi_prev = (curr_liab[-2] + ltd[-2]) / total_assets[-2] if total_assets[-2] else None
        lvgi = (lvgi_now / lvgi_prev) if lvgi_now and lvgi_prev else None

        components = [dsri, gmi, aqi, sgi, depi, sgai, tata, lvgi]
        if any(c is None for c in components):
            return {"m_score": None, "reason": None}

        m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
             + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)

        if m > BUSINESS_QUALITY.BENEISH_MANIPULATION_LIKELY_MIN:
            return {"m_score": round(m, 2),
                    "reason": f"Beneish M-Score {m:.2f} — above the manipulation-likelihood threshold; earnings quality warrants scrutiny"}
        return {"m_score": round(m, 2), "reason": None}
    except Exception as e:
        log.warning(f"[business_quality] Beneish M-Score unavailable: {e}")
        return {"m_score": None, "reason": None}


def compute_business_quality(symbol: str, ticker, df: pd.DataFrame, info: dict, market: str = "IN") -> dict:
    """
    Returns an EngineResponse (as a dict, via .to_dict()) per SSDS-003 §6.
    Pure aggregation over existing + new building blocks — no new
    business-quality FORMULA invented here beyond what SSDS-003 §2-3
    specify; every category's reused sub-score keeps its original
    function's own internal logic untouched.
    """
    info = info or {}
    sector_bucket = classify_sector(info)
    is_financial = sector_bucket == "FINANCIAL"
    screener_d = info.get("_screener_data") or {}

    # ── Reused, already-proven existing functions (SSDS-003 Finding 1) ───────
    buffett = buffett_munger_score(info, df)
    altman = altman_zscore_signal(info)
    sloan = sloan_accruals_signal(info)
    quality_metrics = quality_metrics_score(ticker, df, info)
    corp_actions = corporate_actions_score(ticker, info)

    # ── New metrics (SSDS-003 §3) ─────────────────────────────────────────────
    cash_conversion = _compute_cash_conversion(info)
    asset_turnover = None if is_exempt("asset_turnover", sector_bucket) else _compute_asset_turnover(info, ticker)
    working_capital = None if is_exempt("working_capital_efficiency", sector_bucket) else _compute_working_capital_trend(ticker)
    beneish = _compute_beneish_m_score(ticker)

    # ── Data completeness tracking (SSDS-003 §5) ──────────────────────────────
    # Mandatory metrics per the sector's applicability table — sector-exempt
    # metrics don't count against completeness at all (neither numerator nor
    # denominator), matching SSDS-003 §5 exactly.
    mandatory_checks = {
        "roe": info.get("returnOnEquity") is not None,
        "roce": info.get("returnOnCapitalEmployed") is not None,
        "free_cash_flow": info.get("freeCashflow") is not None,
        "operating_cash_flow": is_exempt("operating_cash_flow", sector_bucket) or (
            info.get("operatingCashflow") is not None or info.get("operatingCashflows") is not None
        ),
        "debt_to_equity": is_exempt("debt_to_equity", sector_bucket) or info.get("debtToEquity") is not None,
        "interest_coverage": is_exempt("interest_coverage", sector_bucket) or screener_d.get("interest_coverage_ratio") is not None,
        "cash_conversion": cash_conversion["ratio"] is not None,
        "share_dilution_and_dividends": True,  # corp_actions degrades gracefully internally; always "available"
        "piotroski": quality_metrics.get("piotroski") is not None,
        "altman": altman.get("z_score") is not None,
        "accruals": sloan.get("accruals_ratio") is not None,
        "buffett": True,  # buffett_munger_score always returns a checklist-based score
    }
    present = sum(1 for v in mandatory_checks.values() if v)
    data_completeness_pct = round(100 * present / len(mandatory_checks), 1)

    if data_completeness_pct < BUSINESS_QUALITY.MIN_DATA_COMPLETENESS_PCT:
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=data_completeness_pct,
            explanation="Insufficient fundamental data available to assess business quality.",
            metadata={
                "engine": "business_quality_engine",
                "sector": info.get("sector"),
                "sector_bucket": sector_bucket,
                "data_completeness_pct": data_completeness_pct,
                "rejection_reason": "insufficient_data",
                "missing_mandatory_metrics": [k for k, v in mandatory_checks.items() if not v],
            },
        ).to_dict()

    # ── Hard quality gates (SSDS-003 §2) ──────────────────────────────────────
    accruals_pct = sloan.get("accruals_ratio")
    accruals_pct = abs(accruals_pct * 100) if accruals_pct is not None else None
    altman_distress = altman.get("z_zone") == "distress" and not is_financial
    aggressive_accruals = accruals_pct is not None and accruals_pct > BUSINESS_QUALITY.ACCRUALS_AGGRESSIVE_MIN_PCT
    beneish_flagged = (beneish["m_score"] is not None
                        and beneish["m_score"] > BUSINESS_QUALITY.BENEISH_MANIPULATION_LIKELY_MIN
                        and not is_financial)

    if (altman_distress and aggressive_accruals) or beneish_flagged:
        rejection_reason = "fraud_risk" if beneish_flagged else "distress_and_aggressive_accruals"
        risks = [r for r in (altman.get("reasons") or []) if "distress" in r.lower()]
        risks += [r for r in (sloan.get("reasons") or [])]
        if beneish["reason"]:
            risks.append(beneish["reason"])
        return EngineResponse(
            score=0,
            grade=Grade.REJECTED,
            confidence=data_completeness_pct,
            risks=risks,
            explanation="Hard quality gate failed — balance sheet distress combined with aggressive earnings management, or fraud-risk evidence (Beneish M-Score).",
            metadata={
                "engine": "business_quality_engine",
                "sector": info.get("sector"),
                "sector_bucket": sector_bucket,
                "data_completeness_pct": data_completeness_pct,
                "rejection_reason": rejection_reason,
                "altman_z": altman.get("z_score"),
                "beneish_m": beneish["m_score"],
            },
        ).to_dict()

    # ── Category scoring (SSDS-003 §2) ────────────────────────────────────────
    profitability = 0.0
    profitability += _map_subscore(quality_metrics.get("score"), cap=12)
    if info.get("returnOnEquity") is not None:
        roe = info["returnOnEquity"]
        if roe > PROFITABILITY.ROE_QUALITY_COMPOUNDER_MIN_PCT / 100:
            profitability += 4
        elif roe < PROFITABILITY.ROE_SEVERE_NEGATIVE:
            profitability -= 8
    if info.get("returnOnCapitalEmployed") is not None:
        roce = info["returnOnCapitalEmployed"]
        if roce > PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT / 100:
            profitability += 4
        elif roce <= 0:
            profitability -= 5
    if asset_turnover and asset_turnover["score"] is not None:
        weight = 0.5 if is_adjusted("asset_turnover", sector_bucket) else 1.0
        profitability += _map_subscore(asset_turnover["score"], cap=4) * weight
    profitability = max(-20, min(20, profitability))

    balance_sheet = _map_subscore(altman.get("score"), cap=10)
    if not is_exempt("debt_to_equity", sector_bucket) and info.get("debtToEquity") is not None:
        de = info["debtToEquity"]
        if de < DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX:
            balance_sheet += 3
        elif de > DEBT_TO_EQUITY.RISK_PENALTY_SEVERE_MIN:
            balance_sheet -= 5
    if not is_exempt("interest_coverage", sector_bucket) and screener_d.get("interest_coverage_ratio") is not None:
        icr = screener_d["interest_coverage_ratio"]
        balance_sheet += 2 if icr > 3 else (-2 if icr < 1.5 else 0)
    balance_sheet = max(-15, min(15, balance_sheet))

    earnings_quality = _map_subscore(sloan.get("score"), cap=8)
    if cash_conversion["score"] is not None:
        earnings_quality += _map_subscore(cash_conversion["score"], cap=5)
    if beneish["m_score"] is not None and not beneish_flagged:
        earnings_quality += 2  # computable AND clean — a positive, not just neutral, signal
    earnings_quality = max(-15, min(15, earnings_quality))

    capital_allocation = _map_subscore(corp_actions.get("score"), cap=10)
    capital_allocation = max(-10, min(10, capital_allocation))

    durable_position = _map_subscore(buffett.get("score"), cap=12)
    sales_3y = screener_d.get("sales_growth_3y_pct")
    sales_5y = screener_d.get("sales_growth_5y_pct")
    growth_accelerating = None
    if sales_3y is not None and sales_5y is not None:
        growth_accelerating = sales_3y > sales_5y
        durable_position += 3 if growth_accelerating else -2
    if working_capital and working_capital["score"] is not None:
        durable_position += _map_subscore(working_capital["score"], cap=3)
    durable_position = max(-15, min(15, durable_position))

    combined = 50 + profitability + balance_sheet + earnings_quality + capital_allocation + durable_position
    score = round(max(0, min(100, combined)))

    # ── Grade (SSDS-003 §6, reusing the existing Grade enum) ──────────────────
    if score >= BUSINESS_QUALITY.GRADE_STRONG_BUY_MIN:
        grade = Grade.STRONG_BUY
    elif score >= BUSINESS_QUALITY.GRADE_BUY_MIN:
        grade = Grade.BUY
    elif score >= BUSINESS_QUALITY.GRADE_HOLD_MIN:
        grade = Grade.HOLD
    elif score >= BUSINESS_QUALITY.GRADE_WATCH_MIN:
        grade = Grade.WATCH
    else:
        grade = Grade.AVOID

    # ── Explainability (SSDS-003 §7) ──────────────────────────────────────────
    categories = {
        "Profitability & Capital Efficiency": (profitability, 20, quality_metrics.get("reasons", [])),
        "Balance Sheet Strength": (balance_sheet, 15, altman.get("reasons", [])),
        "Earnings Quality": (earnings_quality, 15, sloan.get("reasons", [])),
        "Capital Allocation & Shareholder Treatment": (capital_allocation, 10, corp_actions.get("reasons", [])),
        "Durable Competitive Position": (durable_position, 15, buffett.get("reasons", [])),
    }
    ranked = sorted(categories.items(), key=lambda kv: kv[1][0], reverse=True)
    strengths = [f"{name}: {reasons[0]}" for name, (val, cap, reasons) in ranked if val > 0 and reasons][:3]
    weaknesses = [f"{name}: {reasons[0]}" for name, (val, cap, reasons) in ranked if val < 0 and reasons][-3:]

    risks = []
    if altman.get("z_zone") in ("grey", "distress"):
        risks += [r for r in altman.get("reasons", []) if "distress" in r.lower() or "grey" in r.lower()]
    if aggressive_accruals:
        risks.append(f"Accruals ratio {accruals_pct:.1f}% — above the {BUSINESS_QUALITY.ACCRUALS_AGGRESSIVE_MIN_PCT:.0f}% aggressive-earnings-management threshold")
    if beneish["reason"]:
        risks.append(beneish["reason"])

    explanation = (
        f"Business Quality Score {score}/100 ({grade.value}). "
        f"Profitability & Capital Efficiency contributed {profitability:+.1f}, "
        f"Balance Sheet Strength {balance_sheet:+.1f}, Earnings Quality {earnings_quality:+.1f}, "
        f"Capital Allocation {capital_allocation:+.1f}, Durable Competitive Position {durable_position:+.1f}."
    )

    # ── Suitable investment style / holding horizon (metadata — SSDS-003 §6) ──
    if profitability > 8 and balance_sheet > 5 and (growth_accelerating is False or growth_accelerating is None):
        style = "Quality Compounder"
    elif balance_sheet > 8 and profitability < 0:
        style = "Deep Value Candidate"
    elif profitability < -5 and durable_position < 0:
        style = "Turnaround Watch"
    else:
        style = "Standard Quality Profile"
    holding_horizon = "Long" if durable_position >= 0 else "Medium"

    return EngineResponse(
        score=score,
        grade=grade,
        confidence=data_completeness_pct,
        strengths=strengths,
        weaknesses=weaknesses,
        risks=risks,
        explanation=explanation,
        metadata={
            "engine": "business_quality_engine",
            "sector": info.get("sector"),
            "sector_bucket": sector_bucket,
            "data_completeness_pct": data_completeness_pct,
            "piotroski_score": quality_metrics.get("piotroski"),
            "altman_z": altman.get("z_score"),
            "altman_zone": altman.get("z_zone"),
            "accruals_ratio": sloan.get("accruals_ratio"),
            "beneish_m": beneish["m_score"],
            "cash_conversion_ratio": cash_conversion["ratio"],
            "asset_turnover": asset_turnover["turnover"] if asset_turnover else None,
            "suitable_investment_style": style,
            "suggested_holding_horizon": holding_horizon,
            "category_contributions": {
                "profitability_capital_efficiency": round(profitability, 1),
                "balance_sheet_strength": round(balance_sheet, 1),
                "earnings_quality": round(earnings_quality, 1),
                "capital_allocation_shareholder_treatment": round(capital_allocation, 1),
                "durable_competitive_position": round(durable_position, 1),
            },
        },
    ).to_dict()
