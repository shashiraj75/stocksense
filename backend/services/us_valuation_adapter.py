"""
US adapter for the Valuation Intelligence Engine v1 (Epic 004 Sprint #003).

Maps a yfinance Ticker's `.info` dict directly into the engine's
provider-independent `fields` shape — US has every field this engine
needs available as a pre-computed yfinance field, confirmed live during
SSDS-008's own Sprint #001 (trailingPegRatio, enterpriseToEbitda,
priceToBook, dividendYield, payoutRatio, freeCashflow all present and
direct for AAPL), so no derivation logic is required here, unlike
us_growth_adapter.py's CAGR computation from raw financial statements.

Per this sprint's explicit rule ("do not create provider-specific logic
inside the engine"), all yfinance-field-naming knowledge stays in this
adapter; valuation_intelligence_engine.py never sees an `info` dict.
"""


def _field(value):
    return {"value": value} if value is not None else None


def build_us_valuation_fields(info: dict) -> dict:
    """
    `info` is a yfinance.Ticker.info-shaped dict — the same object
    Business Quality's, Financial Strength's, and Growth Intelligence's
    own US adapters already receive in prediction_engine.py, reused here
    rather than triggering a separate fetch.
    """
    info = info or {}
    if not info:
        return {}

    pe_ratio = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    ev_sales = info.get("enterpriseToRevenue")
    price_book = info.get("priceToBook")
    ev_ebitda = info.get("enterpriseToEbitda")
    dividend_yield_pct = info.get("dividendYield")
    payout_ratio = info.get("payoutRatio")
    market_cap = info.get("marketCap")
    peg_ratio = info.get("trailingPegRatio")

    market_cap_val = info.get("marketCap")
    fcf = info.get("freeCashflow")
    fcf_yield_pct = round(100 * fcf / market_cap_val, 2) if (fcf is not None and market_cap_val) else None

    return {
        "pe_ratio": _field(pe_ratio),
        "forward_pe": _field(forward_pe),
        "ev_sales": _field(ev_sales),
        "price_book": _field(price_book),
        "ev_ebitda": _field(ev_ebitda),
        "dividend_yield_pct": _field(dividend_yield_pct),
        "payout_ratio": _field(payout_ratio),
        "market_cap": _field(market_cap),
        "fcf_yield_pct": _field(fcf_yield_pct),
        "peg_ratio": _field(peg_ratio),
    }
