"""
US adapter for the Growth Intelligence Engine v1 (Epic 003 Sprint #003).

Maps a yfinance Ticker's `.financials`/`.balance_sheet` DataFrames into
the engine's provider-independent `fields` shape. Unlike India
(screener.in arrives with pre-computed 3Y/5Y CAGR), US has no
pre-computed growth-rate fields anywhere in this codebase's existing
Data Fabric — yfinance/SEC EDGAR only expose raw multi-year statement
data, so this adapter computes every CAGR itself via the same shared
growth_utils functions india_growth_adapter.py uses, per SSDS-007's own
"Structural difference worth naming explicitly" section.

Per this sprint's explicit rule ("do not create provider-specific logic
inside the engine"), all yfinance-shape-specific row-lookup logic stays
in this adapter; growth_intelligence_engine.py never sees a DataFrame.
"""

from services.growth_utils import compute_cagr_from_series, compute_categorical_trend, compute_coefficient_of_variation


def _get_financial_row(df_like, *labels):
    """Generic row-lookup across a yfinance financials/balance_sheet
    DataFrame, sorted oldest-to-newest. Mirrors business_quality_engine.py's
    identical helper exactly (a generic dataframe-row-lookup utility, not
    a duplicated scoring formula, per that module's own SES-001 rationale)
    — kept as a local copy rather than a cross-engine import to avoid
    coupling Growth Intelligence's adapter to Business Quality's module."""
    if df_like is None or df_like.empty:
        return None
    df_sorted = df_like.sort_index(axis=1)
    for label in labels:
        if label in df_sorted.index:
            row = df_sorted.loc[label].dropna()
            if not row.empty:
                return list(row.sort_index().values)
    return None


def _field(value):
    return {"value": value} if value is not None else None


def build_us_growth_fields(ticker) -> dict:
    """
    `ticker` is a yfinance.Ticker-like object (or the _SharedTickerCache
    wrapper prediction_engine.py already uses) exposing `.financials` and
    `.balance_sheet` — the same object Business Quality's and Deep
    Fundamentals' closures already receive in prediction_engine.py,
    reused here rather than triggering a fourth independent fetch.
    """
    fin = getattr(ticker, "financials", None)
    bs = getattr(ticker, "balance_sheet", None)

    revenue_series = _get_financial_row(fin, "Total Revenue", "Revenue")
    net_income_series = _get_financial_row(fin, "Net Income", "Net Income Common Stockholders")
    op_income_series = _get_financial_row(fin, "Operating Income", "Total Operating Income As Reported")
    diluted_eps_series = _get_financial_row(fin, "Diluted EPS", "Basic EPS")

    long_term_debt_series = _get_financial_row(bs, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
    current_debt_series = _get_financial_row(bs, "Current Debt", "Current Debt And Capital Lease Obligation")
    equity_series = _get_financial_row(bs, "Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest")

    revenue_growth_3y = compute_cagr_from_series(revenue_series, 3)
    revenue_growth_5y = compute_cagr_from_series(revenue_series, 5)
    profit_growth_3y = compute_cagr_from_series(net_income_series, 3)
    profit_growth_5y = compute_cagr_from_series(net_income_series, 5)
    op_profit_growth_3y = compute_cagr_from_series(op_income_series, 3)

    # eps_trend: US has no pre-existing quarterly-PAT-equivalent fetch in
    # this codebase's Data Fabric, so this reuses the identical shared
    # bucketing logic against the only multi-year EPS-adjacent series
    # actually available (annual diluted EPS) — a coarser cadence than
    # India's quarterly signal, named explicitly as an asymmetry in the
    # implementation report, not hidden.
    eps_trend = compute_categorical_trend(diluted_eps_series)

    revenue_growth_cv = compute_coefficient_of_variation(revenue_series)

    invested_capital_series = None
    if long_term_debt_series and equity_series:
        n = min(len(long_term_debt_series), len(equity_series),
                len(current_debt_series) if current_debt_series else len(long_term_debt_series))
        if n >= 4:
            ltd = long_term_debt_series[-n:]
            eq = equity_series[-n:]
            cd = current_debt_series[-n:] if current_debt_series else [0.0] * n
            invested_capital_series = [ltd[i] + eq[i] + cd[i] for i in range(n)]
    invested_capital_growth_3y = compute_cagr_from_series(invested_capital_series, 3)

    # Margin trend: US has no pre-existing margin-series fetch either —
    # computed directly here from operating income / revenue, aligned by
    # the shorter of the two series (both already oldest-to-newest).
    margin_trend_pct_change = None
    if op_income_series and revenue_series:
        n = min(len(op_income_series), len(revenue_series))
        if n >= 4:
            op = op_income_series[-n:]
            rev = revenue_series[-n:]
            margins = [100 * op[i] / rev[i] for i in range(n) if rev[i]]
            if len(margins) >= 4:
                margin_trend_pct_change = round(margins[-1] - margins[0], 2)

    return {
        "revenue_growth_3y_pct": _field(revenue_growth_3y),
        "revenue_growth_5y_pct": _field(revenue_growth_5y),
        "profit_growth_3y_pct": _field(profit_growth_3y),
        "profit_growth_5y_pct": _field(profit_growth_5y),
        "eps_trend": _field(eps_trend),
        "revenue_annual_series": _field(revenue_series),
        "revenue_growth_cv": _field(revenue_growth_cv),
        "operating_profit_growth_3y_pct": _field(op_profit_growth_3y),
        "reinvestment_capital_growth_3y_pct": _field(invested_capital_growth_3y),
        "margin_annual_pct_series": _field(None),  # not separately tracked for US — see margin_trend_pct_change instead
        "margin_trend_pct_change": _field(margin_trend_pct_change),
    }
