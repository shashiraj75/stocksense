"""
Bull / Bear case generator.

Pure, rule-based, NO LLM/AI text — every statement maps to a concrete metric
value (a sub-score, a fundamental ratio, an analyst figure). Each rule fires
only when its underlying metric crosses an explicit threshold, and the metric
value is embedded in the generated string so the claim is auditable.
"""

# Thresholds: a sub-score >= HI is a bull point, <= LO is a bear point.
HI = 65
LO = 35


def _qscore(quality: dict | None, dim: str):
    if not quality:
        return None
    d = quality.get("breakdown", {}).get(dim)
    if isinstance(d, dict):
        return d.get("score")
    return None


def generate_bull_bear_case(
    quality: dict | None,
    fund: dict | None,
    technical: dict | None,
    sentiment: dict | None,
    analyst_score: dict | None,
    week52_score: dict | None,
    info: dict | None = None,
) -> tuple[list[str], list[str]]:
    bull: list[str] = []
    bear: list[str] = []
    info = info or {}

    # ── Quality sub-factors (India market) ──────────────────────────────────
    val = _qscore(quality, "valuation")
    if val is not None:
        if val >= HI:
            bull.append(f"Attractively valued vs sector (valuation score {val}/100)")
        elif val <= LO:
            bear.append(f"Stretched valuation vs sector (valuation score {val}/100)")

    flow = _qscore(quality, "inst_flow")
    if flow is not None:
        if flow >= HI:
            bull.append(f"Institutional accumulation detected (flow score {flow}/100)")
        elif flow <= LO:
            bear.append(f"Institutional distribution detected (flow score {flow}/100)")

    rs = _qscore(quality, "relative_strength")
    if rs is not None:
        if rs >= HI:
            bull.append(f"Outperforming the Nifty 50 benchmark (relative-strength score {rs}/100)")
        elif rs <= LO:
            bear.append(f"Underperforming the Nifty 50 benchmark (relative-strength score {rs}/100)")

    sect = _qscore(quality, "sector_strength")
    if sect is not None:
        if sect >= HI:
            bull.append(f"Sector outperforming the broad market (sector score {sect}/100)")
        elif sect <= LO:
            bear.append(f"Sector lagging the broad market (sector score {sect}/100)")

    er = _qscore(quality, "earnings_revision")
    if er is not None:
        if er >= HI:
            bull.append(f"Positive earnings-revision trend (earnings score {er}/100)")
        elif er <= LO:
            bear.append(f"Negative earnings-revision trend (earnings score {er}/100)")

    risk = _qscore(quality, "risk_management")
    if risk is not None:
        if risk >= HI:
            bull.append(f"Strong risk-adjusted return profile (risk score {risk}/100)")
        elif risk <= LO:
            bear.append(f"Elevated drawdown/volatility risk (risk score {risk}/100)")

    piotroski = quality.get("piotroski") if quality else None
    if piotroski is not None:
        if piotroski >= 7:
            bull.append(f"Piotroski F-Score {piotroski}/9 — strong balance-sheet quality")
        elif piotroski <= 3:
            bear.append(f"Piotroski F-Score {piotroski}/9 — weak balance-sheet fundamentals")

    # ── Raw fundamentals from the ticker info dict (works for US + IN) ───────
    pe = info.get("trailingPE")
    if isinstance(pe, (int, float)) and pe > 0:
        if pe < 15:
            bull.append(f"Low P/E of {pe:.1f} — inexpensive on earnings")
        elif pe > 50:
            bear.append(f"High P/E of {pe:.1f} — richly priced on earnings")

    roe = info.get("returnOnEquity")
    if isinstance(roe, (int, float)):
        if roe > 0.20:
            bull.append(f"Strong return on equity ({roe * 100:.1f}%)")
        elif roe < 0:
            bear.append("Negative return on equity — currently unprofitable")

    de = info.get("debtToEquity")
    if isinstance(de, (int, float)):
        if de > 200:
            bear.append(f"High leverage — debt-to-equity at {de:.0f}%")
        elif de < 40:
            bull.append(f"Low leverage — debt-to-equity at {de:.0f}%")

    rev = info.get("revenueGrowth")
    if isinstance(rev, (int, float)):
        if rev > 0.20:
            bull.append(f"Strong revenue growth ({rev * 100:.1f}% YoY)")
        elif rev < -0.05:
            bear.append(f"Revenue contracting ({rev * 100:.1f}% YoY)")

    fcf = info.get("freeCashflow")
    if isinstance(fcf, (int, float)):
        if fcf > 0:
            bull.append("Positive free cash flow")
        elif fcf < 0:
            bear.append("Negative free cash flow — burning cash")

    # ── Technical / sentiment / analyst / 52-week (direction-only signals) ──
    tech_s = technical.get("score") if technical else None
    if tech_s is not None:
        if tech_s >= HI:
            bull.append(f"Bullish technical setup (technical score {tech_s}/100)")
        elif tech_s <= LO:
            bear.append(f"Bearish technical setup (technical score {tech_s}/100)")

    if sentiment:
        label = sentiment.get("label")
        if label == "BULLISH":
            bull.append(f"Positive news sentiment ({sentiment.get('bullish', 0)} bullish vs {sentiment.get('bearish', 0)} bearish articles)")
        elif label == "BEARISH":
            bear.append(f"Negative news sentiment ({sentiment.get('bullish', 0)} bullish vs {sentiment.get('bearish', 0)} bearish articles)")

    an = analyst_score.get("score") if analyst_score else None
    if an is not None:
        if an >= 60:
            bull.append(f"Favourable analyst consensus (analyst score {an}/100)")
        elif an <= 40:
            bear.append(f"Cautious analyst consensus (analyst score {an}/100)")

    w52 = week52_score.get("score") if week52_score else None
    if w52 is not None:
        if w52 >= HI:
            bull.append(f"Trading strong within its 52-week range (position score {w52}/100)")
        elif w52 <= LO:
            bear.append(f"Trading near 52-week lows (position score {w52}/100)")

    # Cap each list so the UI stays readable; keep the highest-signal first.
    return bull[:6], bear[:6]
