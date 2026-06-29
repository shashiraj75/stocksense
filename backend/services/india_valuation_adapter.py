"""
India adapter for the Valuation Intelligence Engine v1 (Epic 004 Sprint #003).

Maps BOTH the raw dict returned by services.screener_data.fetch_screener_data()
AND a yfinance-style `info` dict (`.NS` suffix) into the engine's
provider-independent `fields` shape — a deliberate dual-provider design,
not a stylistic choice. Sprint #002's India Data Feasibility Study found
screener.in ALONE materially understated India's valuation-data
availability (Forward P/E and Payout Ratio are 0% via screener.in but
100% via yfinance across 113 real companies) — this adapter is built to
the corrected evidence, reusing exactly the yfinance-base/screener-
enrichment pattern prediction_engine.py already uses for India, per that
study's own recommendation, not inventing a new fetch pattern.

Scope, per the India Data Feasibility Study's own evidence: EV/EBITDA,
FCF Yield, and PEG are structurally unavailable for Banks/NBFC (0/10
Banks, 1/9 NBFC in the 113-company sample) — this adapter passes None
for these fields for that population rather than fabricating a value,
exactly per this sprint's "gracefully skip... do not fabricate" rule.
The engine itself, not this adapter, decides applicability from
`sector_bucket` — this adapter's only job is honest field mapping.
"""


def _field(value):
    """Wraps a raw value in the {"value": ...} shape compute_valuation_intelligence
    expects, mirroring growth/financial-strength adapters' own per-field shape."""
    return {"value": value} if value is not None else None


def _fcf_yield_pct(info: dict, screener_data: dict) -> float | None:
    """Free Cash Flow Yield, preferring yfinance's direct `freeCashflow`
    field (confirmed cleaner by Sprint #002 — 81.4% available, no
    CapEx-isolation imprecision) over the screener.in OCF-minus-total-
    investing-CF approximation Growth Intelligence's own adapter already
    uses (inherits that engine's confirmed imprecision when used as the
    fallback)."""
    market_cap = info.get("marketCap")
    fcf = info.get("freeCashflow")
    if fcf is not None and market_cap:
        return round(100 * fcf / market_cap, 2)
    # Fallback: screener.in OCF - total investing CF, in crore, against
    # screener's own market_cap_cr — same approximation Growth
    # Intelligence's india_growth_adapter.py already accepts.
    ocf_series = screener_data.get("operating_cf_annual_cr")
    investing_latest = screener_data.get("investing_cf_latest_cr")
    market_cap_cr = screener_data.get("market_cap_cr")
    if ocf_series and investing_latest is not None and market_cap_cr:
        fcf_approx_cr = ocf_series[-1] + investing_latest  # investing CF is typically negative
        return round(100 * fcf_approx_cr / market_cap_cr, 2)
    return None


def _peg_ratio(info: dict, screener_data: dict) -> float | None:
    """PEG, preferring yfinance's own pre-computed `trailingPegRatio`
    (confirmed rare for India — only 3.5% in Sprint #002's sample, large
    IT/Pharma names) and otherwise computing pe_ratio / 3Y profit growth
    using screener.in's own already-scraped growth field — the same raw
    input Growth Intelligence's own adapter reads from this same
    screener_data dict, not a re-derivation of that engine's verdict, per
    SSDS-008's "read, don't recompute" rule applied to the data this
    sprint's scope (no cross-engine call) can actually reach."""
    peg = info.get("trailingPegRatio")
    if isinstance(peg, (int, float)) and peg > 0:
        return round(peg, 2)
    pe = screener_data.get("pe_ratio")
    growth_3y = screener_data.get("profit_growth_3y_pct")
    # Graceful-degradation finding (Sprint #003 regression testing): a
    # malformed, non-numeric scraped value (screener.in occasionally
    # returns a string artifact on a parse edge case) previously raised
    # TypeError on the `pe > 0` comparison rather than degrading gracefully
    # — isinstance guards here, not a try/except, since this is a known,
    # anticipated provider-data-shape failure mode, not an exceptional one.
    if (isinstance(pe, (int, float)) and pe > 0
            and isinstance(growth_3y, (int, float)) and growth_3y > 0):
        return round(pe / growth_3y, 2)
    return None


def build_india_valuation_fields(screener_data: dict, info: dict) -> dict:
    """
    `screener_data` is the raw dict returned by fetch_screener_data().
    `info` is a yfinance Ticker.info-shaped dict for the `.NS` symbol —
    confirmed by Sprint #002 to carry Forward P/E, Payout Ratio, and
    Price/Book at 100% availability, materially better than screener.in
    alone for those three specific fields.
    """
    screener_data = screener_data or {}
    info = info or {}
    if not screener_data.get("available") and not info:
        return {}

    pe_ratio = screener_data.get("pe_ratio") or info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    ev_sales = screener_data.get("price_to_sales") or info.get("enterpriseToRevenue")
    price_book = info.get("priceToBook")
    ev_ebitda = screener_data.get("ev_ebitda") or info.get("enterpriseToEbitda")
    dividend_yield_pct = screener_data.get("dividend_yield_pct")
    if dividend_yield_pct is None:
        dividend_yield_pct = info.get("dividendYield")
    payout_ratio = info.get("payoutRatio")
    market_cap = screener_data.get("market_cap_cr") or info.get("marketCap")
    fcf_yield_pct = _fcf_yield_pct(info, screener_data)
    peg_ratio = _peg_ratio(info, screener_data)

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
