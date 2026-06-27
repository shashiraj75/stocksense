"""
StockSense360 India Business Quality Adapter (SSDS-003, SSDS-004,
Sprint #007).

Transforms screener.in provider data into the Business Quality Engine's
existing yfinance-`.info`-shaped input model, applying the derivations
validated in the India Fundamentals Data Validation Study (65 companies,
15 sectors — Altman/Sloan/Cash-Conversion/Asset-Turnover all confirmed
at 97-100% completeness). This module contains NO Business Quality
Engine scoring logic of its own — per this sprint's explicit "do not
duplicate engine logic" instruction, it only maps and derives inputs,
then calls the unchanged `compute_business_quality()`.

Mirrors the existing US adapter pattern (`services/us_fundamentals.py`'s
`_build()`, wired in Sprint #004/#005) while respecting the IN-specific
constraint that screener.in — not yfinance — is the primary data source
(SSDS-004 §1). A yfinance `Ticker` is still constructed here, exactly as
`_build()` already does for US: yfinance is an EXISTING, already-used
provider in this codebase (quotes, price history, and — per the
Production Readiness Validation and this study — a partial fallback
role for Piotroski/corporate-actions/Asset-Turnover's own
`ticker.balance_sheet` access inside the Business Quality Engine
itself). Constructing it here is not a new provider and not a
redesign of SSDS-004's provider architecture — it is the same fallback
role SSDS-004 already names for `FallbackProvider`, applied at the
point this adapter needs it, exactly as the India Fundamentals Data
Validation Study's own methodology already did to reach its validated
completeness numbers.
"""

import logging

import pandas as pd
import yfinance as yf

from services.business_quality_engine import compute_business_quality

log = logging.getLogger(__name__)

_CR_TO_UNITS = 1e7  # screener.in figures are in Rupees Crore; the
                     # Business Quality Engine's info-dict convention
                     # (mirroring yfinance) is plain Rupees.


def build_india_info(screener: dict) -> dict:
    """
    Maps screener.in's already-scraped fields into the info-dict shape
    business_quality_engine.py reads, applying the Sprint #006-validated
    derivations. Every field is tagged below with its provenance:

      [DIRECT]       — a 1:1 screener.in field, mapped the same way
                       augment_info_with_screener() already maps it
                       elsewhere in this codebase (not a new mapping
                       decision, reused for consistency).
      [DERIVED/PROVEN]    — Total Assets via the balance-sheet identity
                       (Assets = Liabilities + Equity). India Fundamentals
                       Data Validation Study, Phase 3: cross-checked
                       against an independent source (yfinance) for 65
                       real companies — 63/65 (97%) matched within 3%,
                       median match ~0.01%. Both outliers individually
                       explained (a 2023 merger restatement; a yfinance-
                       side data anomaly specific to one company) — not
                       evidence against the identity.
      [DERIVED/SUPPORTED] — Retained Earnings via Reserves & Surplus, and
                       EBIT via Operating Profit. Supported by indirect
                       evidence (sensible, differentiated Altman zones
                       across the same 65-company study) but not
                       independently cross-checked the way Total Assets
                       was — named explicitly as a Known Limitation, not
                       overstated as Proven.
      [UNAVAILABLE]  — Working Capital (needs Current Assets/Liabilities,
                       confirmed absent from the current scrape) and
                       Beneish's Receivables/SG&A (confirmed, total gap,
                       0/65 in the same study). Left absent deliberately,
                       per SSDS-003 §5's missing-data philosophy:
                       excluded, never guessed. Beneish M-Score is
                       explicitly out of scope for this sprint.
    """
    info: dict = {}

    # ── [DIRECT] classification ───────────────────────────────────────────
    if screener.get("sector_name"):
        info["sector"] = screener["sector_name"]
    if screener.get("industry_name"):
        info["industry"] = screener["industry_name"]

    # ── [DIRECT] core ratios — same mapping as augment_info_with_screener ──
    if screener.get("roe_pct") is not None:
        info["returnOnEquity"] = screener["roe_pct"] / 100
    if screener.get("roce_pct") is not None:
        info["returnOnCapitalEmployed"] = screener["roce_pct"] / 100
    if screener.get("debt_to_equity_pct") is not None:
        info["debtToEquity"] = screener["debt_to_equity_pct"]
    if screener.get("pe_ratio") is not None:
        info["trailingPE"] = screener["pe_ratio"]
    if screener.get("market_cap_cr") is not None:
        info["marketCap"] = screener["market_cap_cr"] * _CR_TO_UNITS
    rev_growth = screener.get("sales_growth_ttm_pct") or screener.get("sales_growth_3y_pct")
    if rev_growth is not None:
        info["revenueGrowth"] = rev_growth / 100

    # ── [DIRECT] cash flow / income — already-scraped fields, promoted to
    # top-level info keys so every Business Quality Engine metric that
    # reads `info` directly (not just sloan_accruals_signal's own existing
    # _screener_data fallback) can see them. Confirmed by the Validation
    # Study (Phase 5) to be the actual reason Cash Conversion already
    # worked for most companies even before this adapter existed — this
    # makes that coverage deliberate and complete rather than incidental.
    if screener.get("operating_cf_latest_cr") is not None:
        ocf = screener["operating_cf_latest_cr"] * _CR_TO_UNITS
        info["operatingCashflow"] = ocf
        info["operatingCashflows"] = ocf
    quarterly_pat = screener.get("quarterly_pat_cr") or []
    if len(quarterly_pat) >= 4:
        info["netIncome"] = sum(quarterly_pat[-4:]) * _CR_TO_UNITS
    if screener.get("sales_latest_cr") is not None:
        info["totalRevenue"] = screener["sales_latest_cr"] * _CR_TO_UNITS
    if screener.get("borrowings_latest_cr") is not None:
        info["totalDebt"] = screener["borrowings_latest_cr"] * _CR_TO_UNITS

    # ── [DERIVED/PROVEN] Total Assets ───────────────────────────────────────
    total_liabilities = screener.get("total_liabilities_annual_cr") or []
    if total_liabilities:
        info["totalAssets"] = total_liabilities[-1] * _CR_TO_UNITS

    # ── [DERIVED/SUPPORTED] EBIT and Retained Earnings ──────────────────────
    if screener.get("operating_profit_latest_cr") is not None:
        ebit = screener["operating_profit_latest_cr"] * _CR_TO_UNITS
        info["ebit"] = ebit
        info["operatingIncome"] = ebit
    if screener.get("reserves_latest_cr") is not None:
        info["retainedEarnings"] = screener["reserves_latest_cr"] * _CR_TO_UNITS

    # ── _screener_data sub-dict — the same shape sloan_accruals_signal's
    # existing fallback and business_quality_engine.py's sector-applicability
    # / growth-acceleration checks already expect.
    info["_screener_data"] = {
        "interest_coverage_ratio": screener.get("interest_coverage_ratio"),
        "sales_growth_3y_pct": screener.get("sales_growth_3y_pct"),
        "sales_growth_5y_pct": screener.get("sales_growth_5y_pct"),
        "operating_cf_latest_cr": screener.get("operating_cf_latest_cr"),
        "operating_cf_annual_cr": screener.get("operating_cf_annual_cr"),
        "quarterly_pat_cr": screener.get("quarterly_pat_cr"),
    }

    return info


def compute_india_business_quality(symbol: str, screener: dict, market: str = "IN") -> dict | None:
    """
    The India Business Quality Adapter's single public entry point.

    Takes already-fetched screener.in data (callers — the nightly IN
    refresh job — already have this in hand; this function deliberately
    does not re-fetch it, per the "no unnecessary provider calls"
    requirement) and an unmodified `compute_business_quality()` call.

    `df` is an empty DataFrame, not a fetched price history — confirmed
    in Sprint #004/#005 by reading both `buffett_munger_score`'s and
    `quality_metrics_score`'s source that neither function uses the `df`
    parameter at all; fetching real price history here would cost a
    network call for zero signal, exactly as already established for the
    US adapter.

    Returns None (not a guessed/partial result) if screener data is
    unavailable for this symbol — the caller's existing screener-only
    behavior is the correct fallback, not a Business Quality Engine
    default.
    """
    if not screener or not screener.get("available"):
        return None

    try:
        info = build_india_info(screener)
        ticker = yf.Ticker(symbol + ".NS")  # constructing this is lazy —
                                             # no network call until a
                                             # specific attribute (e.g.
                                             # .balance_sheet) is read
                                             # inside compute_business_quality
        return compute_business_quality(symbol, ticker, pd.DataFrame(), info, market=market)
    except Exception as e:
        log.warning(f"[india_bq_adapter] failed for {symbol}: {e}")
        return None
