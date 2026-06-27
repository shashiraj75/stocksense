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

from services.thresholds import DEBT_TO_EQUITY, PROFITABILITY, GROWTH, VALUATION, GOVERNANCE, CASH_FLOW


def _check(label: str, passed: bool) -> dict:
    return {"label": label, "passed": passed}


def compute_scorecard(stock: dict, market: str = "IN") -> dict:
    roe = stock.get("roe_pct")
    roe_avg = stock.get("roe_5y_pct")  # 5Y avg for IN, 4Y avg for US
    roce = stock.get("roce_pct")
    sales_3y = stock.get("sales_growth_3y_pct")
    sales_5y = stock.get("sales_growth_5y_pct")  # always None for US — see below
    profit_3y = stock.get("profit_growth_3y_pct")
    profit_5y = stock.get("profit_growth_5y_pct")  # always None for US — see below
    de = stock.get("debt_to_equity_pct")
    icr = stock.get("interest_coverage_ratio")
    ocf = stock.get("operating_cf_latest_cr")
    pe = stock.get("pe_ratio")
    ev_ebitda = stock.get("ev_ebitda")
    pledge = stock.get("promoter_pledge_pct")
    roe_avg_label = "4Y avg" if market == "US" else "5Y avg"

    checks = [
        # Business Quality
        _check(f"ROE > 18%, not visibly declining vs {roe_avg_label}", roe is not None and roe > PROFITABILITY.ROE_QUALITY_COMPOUNDER_MIN_PCT and (roe_avg is None or roe >= roe_avg * 0.8)),
        _check("ROCE > 15%", roce is not None and roce > PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT),
        _check("Profit growing both 3Y and 5Y", profit_3y is not None and profit_3y > 0 and (profit_5y is None or profit_5y > 0)),
        # Growth
        _check("Sales growth > 12% (3Y)", sales_3y is not None and sales_3y > GROWTH.SALES_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT),
        _check("Profit growth > 12% (3Y)", profit_3y is not None and profit_3y > GROWTH.PROFIT_GROWTH_3Y_QUALITY_COMPOUNDER_MIN_PCT),
        # Financial Safety
        _check("Debt/Equity < 50%", de is not None and de < DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX),
        _check("Interest Coverage > 3x", icr is not None and icr > GOVERNANCE.INTEREST_COVERAGE_MIN),
        _check("Operating cash flow positive (latest year)", ocf is not None and ocf > CASH_FLOW.OCF_MUST_BE_POSITIVE),
        # Valuation
        _check("P/E < 35", pe is not None and pe < VALUATION.PE_QUALITY_COMPOUNDER_MAX),
        _check("EV/EBITDA < 20", ev_ebitda is not None and ev_ebitda < VALUATION.EV_EBITDA_QUALITY_COMPOUNDER_MAX),
    ]

    if market == "IN":
        # Both checks below need data that's structurally unavailable for
        # US stocks (no 5Y growth history past yfinance's 4Y cap, and no
        # "promoter pledge" concept in US filings at all) — included only
        # for IN rather than silently auto-passing or always-failing them.
        checks.append(_check("Growth accelerating (3Y CAGR > 5Y CAGR)", sales_3y is not None and sales_5y is not None and sales_3y > sales_5y))
        checks.append(_check("No promoter pledge (latest)", pledge is None or pledge < GOVERNANCE.PROMOTER_PLEDGE_CLEAN_MAX_PCT))

    max_score = len(checks)
    score = sum(1 for c in checks if c["passed"])

    # Anti-loss red flags — any ONE triggers a downgrade to Avoid regardless
    # of scorecard total, matching "one red flag = Watch, two = Exit" intent
    # (collapsed to a single override here since we only have a snapshot,
    # not the multi-year deterioration this rule is designed to catch).
    red_flags = []
    if roe is not None and roe_avg is not None and roe < roe_avg * 0.6:
        red_flags.append(f"ROE well below its {roe_avg_label} — possible earnings deterioration")
    if profit_3y is not None and profit_3y < 0:
        red_flags.append("3Y profit growth is negative")
    if ocf is not None and ocf < 0:
        red_flags.append("Negative operating cash flow (latest year)")
    if market == "IN" and pledge is not None and pledge > GOVERNANCE.PROMOTER_PLEDGE_RED_FLAG_MIN_PCT:
        red_flags.append(f"Promoter pledge at {pledge:.1f}% (latest)")
    if de is not None and de > DEBT_TO_EQUITY.ELEVATED_PENALTY_MIN:
        red_flags.append(f"High leverage — Debt/Equity {de:.0f}% (latest)")

    # Verdict thresholds scale proportionally to max_score (10/12=0.83,
    # 7/12=0.58) so a 10-check US scorecard and a 12-check IN one apply the
    # same relative bar, not the same absolute one.
    if red_flags:
        verdict = "avoid" if len(red_flags) >= 2 else "watch"
    elif score >= round(max_score * 0.83):
        verdict = "strong_buy"
    elif score >= round(max_score * 0.58):
        verdict = "watchlist"
    else:
        verdict = "avoid"

    # Explicit "elite_strong_buy" — a stricter, all-conditions-must-pass rule
    # on top of the score-percentage verdict above, requested explicitly:
    # ROCE > 15%, Debt/Equity < 50%, OCF > 0, Sales growth > 10%. The
    # founder's original formula also wanted Order Book/Revenue > 3x, which
    # we dropped — no scraped source has that figure for any stock, IN or
    # US (not on screener.in, not in yfinance); faking it would be worse
    # than omitting it. Only promotes a verdict that already cleared
    # "strong_buy" or "watchlist" — never overrides "avoid"/"watch", since
    # the Anti-Loss red-flag check is a hard ceiling by design (see above).
    elite_strong_buy = (
        roce is not None and roce > PROFITABILITY.ROCE_QUALITY_COMPOUNDER_MIN_PCT
        and de is not None and de < DEBT_TO_EQUITY.QUALITY_COMPOUNDER_MAX
        and ocf is not None and ocf > CASH_FLOW.OCF_MUST_BE_POSITIVE
        and sales_3y is not None and sales_3y > GROWTH.SALES_GROWTH_3Y_ELITE_MIN_PCT
    )
    if elite_strong_buy and verdict in ("strong_buy", "watchlist"):
        verdict = "elite_strong_buy"

    return {
        "score": score,
        "max_score": max_score,
        "verdict": verdict,
        "checks": checks,
        "red_flags": red_flags,
        "elite_strong_buy": elite_strong_buy,
    }


def annotate_and_rank(results: list[dict], market: str = "IN") -> list[dict]:
    """
    Attaches a scorecard to each result, sorts by score descending (the
    "10-20% shortlist" ranking — a screen's pass/fail filter alone doesn't
    tell you which passers are strongest), and marks the top ~20% (at
    least 1, capped reasonably) as the shortlist tier.

    Two things sorting by raw score alone gets wrong, both fixed here:
    1. Ties — a stable sort would let a red-flagged stock outrank an
       equally-scored clean one purely because of list order. Red flag
       count is a secondary sort key so clean stocks win ties.
    2. Eligibility — an "avoid" verdict (2+ red flags) must never be
       shortlisted regardless of raw score, or a row can show a
       "Shortlisted" flame next to its own "Avoid" badge — a direct
       self-contradiction a user would reasonably read as a bug.
    """
    for r in results:
        r["scorecard"] = compute_scorecard(r, market)

    results.sort(key=lambda r: (r["scorecard"]["score"], -len(r["scorecard"]["red_flags"])), reverse=True)

    for r in results:
        r["shortlisted"] = False

    eligible = [r for r in results if r["scorecard"]["verdict"] != "avoid"]
    shortlist_n = max(1, round(len(eligible) * 0.2)) if eligible else 0
    for r in eligible[:shortlist_n]:
        r["shortlisted"] = True

    return results
