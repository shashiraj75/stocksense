"""
StockSense360 US Financial Strength Adapter
(SSDS-005, SSDS-006, Epic 002 Sprint #008).

The ONLY module that performs provider I/O for the Financial Strength
Engine's US path — mirrors exactly the boundary
india_business_quality_adapter.py and us_fundamentals.py already
establish for Business Quality: an adapter fetches from real providers
and normalizes/resolves into the engine's input shape; the engine
itself (financial_strength_engine.py) never imports a provider.

Orchestrates, per SSDS-006's layered architecture:
  1. Provider Adapter layer — sec_edgar_adapter.fetch_us_fundamentals_sec_edgar()
     (already exists, Sprint #004) + a yfinance fetch for the same 16
     unified fields (new this sprint, scoped to this adapter only —
     does not touch services/us_fundamentals.py, per this sprint's
     "preserve backward compatibility" / "do not modify yfinance" rule;
     this is new, additive code, not a change to existing code).
  2. Resolution layer — services.us_provider_precedence.resolve_field()
     (already exists, Sprint #006), per field, using the company's
     sector bucket (services.sector_quality_applicability.classify_sector(),
     already exists, Sprint #004 — reused here, not duplicated, per
     SES-002 §2).
  3. Engine call — financial_strength_engine.compute_financial_strength().
"""

import logging

import yfinance as yf

from services.sector_quality_applicability import classify_sector
from services.financial_strength_engine import compute_financial_strength
from services import sec_edgar_adapter as sea
from services import us_provider_precedence as upp

log = logging.getLogger(__name__)


def _clean(v):
    """NaN is not None — pandas readily returns it for missing cells.
    Also casts numpy scalar types (numpy.float64, numpy.bool_, etc. —
    confirmed live this sprint to leak out of pandas/yfinance reads) to
    plain Python types, since a metadata dict containing a numpy scalar
    is not JSON-serializable by a standard encoder (FastAPI's default
    included) — a real correctness concern for any future API route or
    cache write, not just a cosmetic one."""
    if v is None or v != v:
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def _row(df, label):
    if df is None or df.empty or label not in df.index:
        return None
    series = df.loc[label].dropna()
    if series.empty:
        return None
    return _clean(series.iloc[0])


def fetch_yfinance_unified_fields(ticker) -> tuple[dict, dict]:
    """
    Extracts the same 16 SSDS-005-required unified fields from a
    yfinance Ticker's `.info`/`.balance_sheet`/`.cashflow`/`.financials`
    — the same field-to-row mapping confirmed live across Epic 002's
    Sprint #005/#007 validation work, now promoted from a one-off
    research script into production code.

    Returns (field_values, raw_info) — raw_info is handed to
    classify_sector(), which already expects a yfinance-`.info`-shaped
    dict (reused, not duplicated).
    """
    info = ticker.info or {}
    bs = ticker.balance_sheet
    cf = ticker.cashflow
    fin = ticker.financials

    values = {
        "revenue": _clean(info.get("totalRevenue")) or _row(fin, "Total Revenue"),
        "net_income": _row(fin, "Net Income"),
        "ebit": _row(fin, "EBIT") or _row(fin, "Operating Income"),
        "interest_expense": _row(fin, "Interest Expense"),
        "cash_and_equivalents": _clean(info.get("totalCash")),
        "current_assets": _row(bs, "Current Assets"),
        "current_liabilities": _row(bs, "Current Liabilities"),
        "total_assets": _row(bs, "Total Assets"),
        "total_liabilities": _row(bs, "Total Liabilities Net Minority Interest"),
        "short_term_debt": _row(bs, "Current Debt"),
        "long_term_debt": _row(bs, "Long Term Debt"),
        "total_debt": _clean(info.get("totalDebt")) or _row(bs, "Total Debt"),
        "operating_cash_flow": _row(cf, "Operating Cash Flow") or _clean(info.get("operatingCashflow")),
        "capital_expenditure": _row(cf, "Capital Expenditure"),
        "free_cash_flow": _row(cf, "Free Cash Flow") or _clean(info.get("freeCashflow")),
        "shareholders_equity": _row(bs, "Stockholders Equity"),
    }
    return values, info


def build_us_financial_strength_fields(symbol: str) -> dict:
    """
    Fetches both providers, resolves every field via the finalized
    precedence module, and returns
    {"fields": {...}, "sector_bucket": ..., "symbol": ..., "company_name": ...}
    — ready to hand directly to compute_financial_strength().

    Degrades gracefully (SSDS-006 §2, Fail-Soft Engineering) on any
    single-provider failure: a failed EDGAR fetch still allows yfinance-
    only resolution (and vice versa), never an exception that would
    break a caller iterating many symbols.
    """
    sym = symbol.upper().strip()

    try:
        edgar_result = sea.fetch_us_fundamentals_sec_edgar(sym)
    except Exception as e:
        log.warning("[us_fs_adapter] SEC EDGAR fetch failed for %s: %s", sym, e)
        edgar_result = {"available": False}
    edgar_fields_raw = edgar_result.get("fields", {}) if edgar_result.get("available") else {}

    try:
        ticker = yf.Ticker(sym)
        yfinance_values, info = fetch_yfinance_unified_fields(ticker)
    except Exception as e:
        log.warning("[us_fs_adapter] yfinance fetch failed for %s: %s", sym, e)
        yfinance_values, info = {}, {}

    sector_bucket = classify_sector(info)
    # Confirmed defect, found live during this sprint's required ≥50-company
    # validation: classify_sector() can misclassify a REIT as MANUFACTURING
    # when its yfinance industry label contains "industrial" (e.g. real,
    # observed live: PLD's industry is "REIT - Industrial", PSA's is
    # "REIT - Industrial" too) — MANUFACTURING's bare "industrial" keyword
    # matches before REAL_ESTATE's own patterns are ever checked. Not fixed
    # in sector_quality_applicability.py itself this sprint: that module is
    # Business Quality Engine-owned, and changing its classification would
    # silently change BQE's own already-shipped sector_bucket (and therefore
    # scoring/exemptions) for every REIT in production — an uncontrolled
    # blast radius outside this sprint's scope ("preserve Business Quality
    # Engine boundaries"). Applying a narrow, LOCAL override here instead,
    # since this engine's v1 explicitly excludes REITs and a classifier gap
    # that lets one slip through directly contradicts that stated scope.
    text = f"{info.get('sector') or ''} {info.get('industry') or ''}".lower()
    if "reit" in text:
        sector_bucket = "REAL_ESTATE"

    resolved_fields = {}
    for field in upp.FIELD_PRECEDENCE:
        edgar_record = edgar_fields_raw.get(field)
        if edgar_record and edgar_record.get("derivation_status") == "UNAVAILABLE":
            edgar_record = None
        yfinance_value = yfinance_values.get(field)
        resolved_fields[field] = upp.resolve_field(field, edgar_record, yfinance_value, sector_bucket=sector_bucket)

    return {
        "symbol": sym,
        "company_name": info.get("longName") or info.get("shortName") or edgar_result.get("company_name"),
        "sector_bucket": sector_bucket,
        "fields": resolved_fields,
    }


def compute_us_financial_strength(symbol: str) -> dict:
    """
    The adapter's single public entry point — fetches, resolves, and
    scores a US symbol's Financial Strength in one call. Mirrors the
    shape of india_business_quality_adapter.compute_india_business_quality()'s
    role for its own engine.
    """
    built = build_us_financial_strength_fields(symbol)
    return compute_financial_strength(
        symbol=built["symbol"],
        fields=built["fields"],
        sector_bucket=built["sector_bucket"],
        market="US",
    )
