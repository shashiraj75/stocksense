"""
Decision-support layer on top of the Multibagger Screen's raw filter results:
a transparent, rule-based scorecard (not the AI's ML-weighted score — this is
a deliberately separate, fully-visible checklist), an Anti-Loss red-flag
override, and the "10-20% shortlist" tiering. All computed from fields
already in stock_fundamentals_cache — no new scraping.

Honesty note: several "trend" checks the underlying methodology calls for
(debt rising, pledge rising, cash flow repeatedly negative) need multi-year
history we don't cache (only the latest value). Those checks are
deliberately implemented as latest-snapshot checks instead, and labelled as
such in the reason text — they are NOT claiming a trend we can't see.
"""

SCORECARD_MAX = 12


def _check(label: str, passed: bool) -> dict:
    return {"label": label, "passed": passed}


def compute_scorecard(stock: dict) -> dict:
    roe = stock.get("roe_pct")
    roe_5y = stock.get("roe_5y_pct")
    roce = stock.get("roce_pct")
    sales_3y = stock.get("sales_growth_3y_pct")
    sales_5y = stock.get("sales_growth_5y_pct")
    profit_3y = stock.get("profit_growth_3y_pct")
    profit_5y = stock.get("profit_growth_5y_pct")
    de = stock.get("debt_to_equity_pct")
    icr = stock.get("interest_coverage_ratio")
    ocf = stock.get("operating_cf_latest_cr")
    pe = stock.get("pe_ratio")
    ev_ebitda = stock.get("ev_ebitda")
    pledge = stock.get("promoter_pledge_pct")

    checks = [
        # Business Quality
        _check("ROE > 18%, not visibly declining vs 5Y avg", roe is not None and roe > 18 and (roe_5y is None or roe >= roe_5y * 0.8)),
        _check("ROCE > 15%", roce is not None and roce > 15),
        _check("Profit growing both 3Y and 5Y", profit_3y is not None and profit_3y > 0 and (profit_5y is None or profit_5y > 0)),
        # Growth
        _check("Sales growth > 12% (3Y)", sales_3y is not None and sales_3y > 12),
        _check("Profit growth > 12% (3Y)", profit_3y is not None and profit_3y > 12),
        _check("Growth accelerating (3Y CAGR > 5Y CAGR)", sales_3y is not None and sales_5y is not None and sales_3y > sales_5y),
        # Financial Safety
        _check("Debt/Equity < 50%", de is not None and de < 50),
        _check("Interest Coverage > 3x", icr is not None and icr > 3),
        _check("Operating cash flow positive (latest year)", ocf is not None and ocf > 0),
        # Valuation
        _check("P/E < 35", pe is not None and pe < 35),
        _check("EV/EBITDA < 20", ev_ebitda is not None and ev_ebitda < 20),
        _check("No promoter pledge (latest)", pledge is None or pledge < 1),
    ]

    score = sum(1 for c in checks if c["passed"])

    # Anti-loss red flags — any ONE triggers a downgrade to Avoid regardless
    # of scorecard total, matching "one red flag = Watch, two = Exit" intent
    # (collapsed to a single override here since we only have a snapshot,
    # not the multi-year deterioration this rule is designed to catch).
    red_flags = []
    if roe is not None and roe_5y is not None and roe < roe_5y * 0.6:
        red_flags.append("ROE well below its 5Y average — possible earnings deterioration")
    if profit_3y is not None and profit_3y < 0:
        red_flags.append("3Y profit growth is negative")
    if ocf is not None and ocf < 0:
        red_flags.append("Negative operating cash flow (latest year)")
    if pledge is not None and pledge > 5:
        red_flags.append(f"Promoter pledge at {pledge:.1f}% (latest)")
    if de is not None and de > 150:
        red_flags.append(f"High leverage — Debt/Equity {de:.0f}% (latest)")

    if red_flags:
        verdict = "avoid" if len(red_flags) >= 2 else "watch"
    elif score >= 10:
        verdict = "strong_buy"
    elif score >= 7:
        verdict = "watchlist"
    else:
        verdict = "avoid"

    return {
        "score": score,
        "max_score": SCORECARD_MAX,
        "verdict": verdict,
        "checks": checks,
        "red_flags": red_flags,
    }


def annotate_and_rank(results: list[dict]) -> list[dict]:
    """
    Attaches a scorecard to each result, sorts by score descending (the
    "10-20% shortlist" ranking — a screen's pass/fail filter alone doesn't
    tell you which passers are strongest), and marks the top ~20% (at
    least 1, capped reasonably) as the shortlist tier.
    """
    for r in results:
        r["scorecard"] = compute_scorecard(r)

    results.sort(key=lambda r: r["scorecard"]["score"], reverse=True)

    shortlist_n = max(1, round(len(results) * 0.2)) if results else 0
    for i, r in enumerate(results):
        r["shortlisted"] = i < shortlist_n

    return results
