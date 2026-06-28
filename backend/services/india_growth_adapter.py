"""
India adapter for the Growth Intelligence Engine v1 (Epic 003 Sprint #003).

Maps the raw dict returned by services.screener_data.fetch_screener_data()
into the engine's provider-independent `fields` shape — mirrors exactly
how india_business_quality_adapter.py shapes screener.in data for Business
Quality, and how us_financial_strength_adapter.py resolves yfinance/SEC
EDGAR data for Financial Strength. The engine itself never imports this
module's inputs directly.

Scope, per the India Growth Feasibility Study's own evidence: only the
fields confirmed available are mapped. Banks/NBFCs structurally lack
`operating_profit_annual_cr` (confirmed: 10/85 in the feasibility
study's live sample, all banks/NBFCs) — this adapter passes None for
every metric that depends on it (Operating Profit Growth, Reinvestment
Efficiency) rather than fabricating a value, exactly per this sprint's
explicit "gracefully skip... do not fabricate" rule.
"""

from services.growth_utils import compute_cagr_from_series, compute_coefficient_of_variation


def _field(value):
    """Wraps a raw value in the {"value": ...} shape compute_growth_intelligence
    expects, mirroring financial_strength_engine.py's own per-field shape."""
    return {"value": value} if value is not None else None


def _aligned_invested_capital_series(screener_data: dict) -> list[float] | None:
    """
    Invested capital ≈ total debt + total equity, reconstructed from
    screener.in's separately-scraped reserves/equity-capital/borrowings
    annual series. All three must be present and the same length to align
    by index — confirmed in the Feasibility Study that all three are
    scraped from the same balance-sheet table and share fiscal-year
    columns, so index-alignment (not date-matching) is the correct join.
    Returns None if any series is missing or empty (no fabricated partial
    sums) — this is the same population that lacks Operating Profit
    (banks/NBFCs), confirmed but not re-derived here; this function simply
    returns None for them like everything else does.
    """
    reserves = screener_data.get("reserves_annual_cr")
    equity_capital = screener_data.get("equity_capital_cr")
    borrowings = screener_data.get("borrowings_annual_cr")
    if not reserves or not equity_capital or not borrowings:
        return None
    n = min(len(reserves), len(equity_capital), len(borrowings))
    if n < 4:
        return None
    return [reserves[-n:][i] + equity_capital[-n:][i] + borrowings[-n:][i] for i in range(n)]


def build_india_growth_fields(screener_data: dict) -> dict:
    """
    `screener_data` is the raw dict returned by fetch_screener_data() —
    NOT the curated `_screener_data` sub-dict augment_info_with_screener
    attaches to `info` (that sub-dict carries a narrower, BQE/Multibagger-
    specific field subset; Growth Intelligence needs the full multi-year
    arrays only the raw fetch return carries, confirmed during the
    Feasibility Study).
    """
    if not screener_data or not screener_data.get("available"):
        return {}

    revenue_series = screener_data.get("sales_annual_cr")
    op_profit_series = screener_data.get("operating_profit_annual_cr")
    margin_series = screener_data.get("opm_annual_pct")
    quarterly_pat = screener_data.get("quarterly_pat_cr")

    op_profit_growth_3y = compute_cagr_from_series(op_profit_series, 3)
    invested_capital_series = _aligned_invested_capital_series(screener_data)
    invested_capital_growth_3y = compute_cagr_from_series(invested_capital_series, 3)

    revenue_growth_cv = compute_coefficient_of_variation(revenue_series)

    margin_trend_pct_change = None
    if margin_series and len(margin_series) >= 4:
        margin_trend_pct_change = round(margin_series[-1] - margin_series[0], 2)

    # eps_trend is computed by augment_info_with_screener from the same
    # quarterly_pat series this raw fetch already returns — reusing the
    # identical shared utility (growth_utils.compute_categorical_trend)
    # rather than re-deriving a second copy here, per SES-001 "one
    # computation, one owner". Imported locally to avoid a module-level
    # circular import with screener_data.py (which imports growth_utils).
    from services.growth_utils import compute_categorical_trend
    eps_trend = compute_categorical_trend(quarterly_pat)

    return {
        "revenue_growth_3y_pct": _field(screener_data.get("sales_growth_3y_pct")),
        "revenue_growth_5y_pct": _field(screener_data.get("sales_growth_5y_pct")),
        "profit_growth_3y_pct": _field(screener_data.get("profit_growth_3y_pct")),
        "profit_growth_5y_pct": _field(screener_data.get("profit_growth_5y_pct")),
        "eps_trend": _field(eps_trend),
        "revenue_annual_series": _field(revenue_series),
        "revenue_growth_cv": _field(revenue_growth_cv),
        "operating_profit_growth_3y_pct": _field(op_profit_growth_3y),
        "reinvestment_capital_growth_3y_pct": _field(invested_capital_growth_3y),
        "margin_annual_pct_series": _field(margin_series),
        "margin_trend_pct_change": _field(margin_trend_pct_change),
    }
