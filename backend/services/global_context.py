"""
Global macro context for Indian market predictions.

Fetches US markets, crude oil, gold, USD/INR, VIX, Asian markets, and more.
Results are cached in memory for 15 minutes — all parallel prediction calls share
one fetch cycle, so this adds ~2 seconds of startup cost, not 300×.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

_lock = threading.Lock()
_cache: dict | None = None
_cache_expiry: float = 0
CACHE_TTL = 900  # 15 minutes

# ── Tickers to fetch ─────────────────────────────────────────────────────────
GLOBAL_TICKERS = {
    "sp500":       "^GSPC",      # US S&P 500
    "nasdaq":      "^IXIC",      # NASDAQ Composite
    "dow":         "^DJI",       # Dow Jones Industrial
    "vix":         "^VIX",       # US Fear Index
    "india_vix":   "^INDIAVIX",  # India VIX
    "nikkei":      "^N225",      # Japan Nikkei 225
    "hangseng":    "^HSI",       # Hong Kong Hang Seng
    "crude_brent": "BZ=F",       # Brent Crude Oil
    "gold":        "GC=F",       # Gold Futures
    "usdinr":      "INR=X",      # USD/INR rate (higher = weaker INR)
    "dxy":         "DX-Y.NYB",   # US Dollar Index
    "us10y":       "^TNX",       # US 10-Year Treasury Yield
    "nifty_it":    "^CNXIT",     # Nifty IT sector index
    "nifty_bank":  "^NSEBANK",   # Nifty Bank index
}

# ── Per-stock macro sensitivity for Nifty 100 ────────────────────────────────
# Values: "tailwind" (factor up = good), "headwind" (factor up = bad), "neutral"
STOCK_MACRO_SENSITIVITY: dict[str, dict[str, str]] = {
    # IT exporters — weak INR boosts USD revenue in INR terms; US market health = demand
    "TCS":        {"usdinr": "tailwind", "sp500": "tailwind", "nasdaq": "tailwind", "nifty_it": "tailwind"},
    "INFY":       {"usdinr": "tailwind", "sp500": "tailwind", "nasdaq": "tailwind", "nifty_it": "tailwind"},
    "WIPRO":      {"usdinr": "tailwind", "sp500": "tailwind", "nasdaq": "tailwind", "nifty_it": "tailwind"},
    "HCLTECH":    {"usdinr": "tailwind", "sp500": "tailwind", "nasdaq": "tailwind", "nifty_it": "tailwind"},
    "TECHM":      {"usdinr": "tailwind", "sp500": "tailwind", "nifty_it": "tailwind"},
    "MPHASIS":    {"usdinr": "tailwind", "sp500": "tailwind", "nifty_it": "tailwind"},
    "PERSISTENT": {"usdinr": "tailwind", "sp500": "tailwind", "nifty_it": "tailwind"},
    "OFSS":       {"usdinr": "tailwind", "nifty_it": "tailwind"},
    "LTIM":       {"usdinr": "tailwind", "sp500": "tailwind", "nifty_it": "tailwind"},
    "NAUKRI":     {"sp500": "tailwind"},  # tech hiring index
    # Oil & Gas producers — high crude = more revenue
    "ONGC":       {"crude_brent": "tailwind"},
    "GAIL":       {"crude_brent": "neutral"},
    "OIL":        {"crude_brent": "tailwind"},
    "NMDC":       {"crude_brent": "neutral"},
    # Oil marketing companies (OMCs) — high crude = margin squeeze / under-recovery
    "BPCL":       {"crude_brent": "headwind"},
    "HINDPETRO":  {"crude_brent": "headwind"},
    "IOC":        {"crude_brent": "headwind"},
    # Paints — TiO2 and petrochemical derivatives are crude-linked inputs
    "ASIANPAINT": {"crude_brent": "headwind"},
    "PIDILITIND": {"crude_brent": "headwind"},
    # Gold-sensitive — jewellery demand, gold loan collateral
    "TITAN":      {"gold": "tailwind"},
    "MUTHOOTFIN": {"gold": "tailwind"},
    # Pharma exporters — USD revenue; weak INR = more INR profit
    "SUNPHARMA":  {"usdinr": "tailwind"},
    "DRREDDY":    {"usdinr": "tailwind"},
    "CIPLA":      {"usdinr": "tailwind"},
    "DIVISLAB":   {"usdinr": "tailwind"},
    "LUPIN":      {"usdinr": "tailwind"},
    "TORNTPHARM": {"usdinr": "tailwind"},
    # Auto — crude raises consumer fuel costs & manufacturing input (plastic, rubber)
    "TATAMOTORS": {"crude_brent": "headwind", "sp500": "tailwind"},  # JLR = UK/US exposure
    "MARUTI":     {"crude_brent": "headwind"},
    "M&M":        {"crude_brent": "headwind"},
    "EICHERMOT":  {"crude_brent": "headwind"},
    "TVSMOTOR":   {"crude_brent": "headwind"},
    "HEROMOTOCO": {"crude_brent": "headwind"},
    "BAJAJ-AUTO": {"crude_brent": "headwind"},
    # Metals & Mining — global demand/China exposure
    "TATASTEEL":  {"hangseng": "tailwind", "crude_brent": "neutral"},
    "JSWSTEEL":   {"hangseng": "tailwind"},
    "SAIL":       {"hangseng": "tailwind"},
    "HINDALCO":   {"hangseng": "tailwind", "sp500": "tailwind"},
    "VEDL":       {"hangseng": "tailwind"},
    # Cement — energy cost (petcoke / coal) tracks crude loosely
    "ULTRACEMCO": {"crude_brent": "headwind"},
    "SHREECEM":   {"crude_brent": "headwind"},
    "GRASIM":     {"crude_brent": "headwind"},
    # Banking & Finance — FII outflows when VIX spikes or DXY rises
    "HDFCBANK":   {"vix": "headwind", "dxy": "headwind", "nifty_bank": "tailwind"},
    "ICICIBANK":  {"vix": "headwind", "dxy": "headwind", "nifty_bank": "tailwind"},
    "SBIN":       {"vix": "headwind", "nifty_bank": "tailwind"},
    "KOTAKBANK":  {"vix": "headwind", "nifty_bank": "tailwind"},
    "AXISBANK":   {"vix": "headwind", "nifty_bank": "tailwind"},
    "BAJFINANCE": {"vix": "headwind", "nifty_bank": "tailwind"},
    "BAJAJFINSV": {"vix": "headwind", "nifty_bank": "tailwind"},
    "INDUSINDBK": {"vix": "headwind", "nifty_bank": "tailwind"},
    "BANDHANBNK": {"vix": "headwind"},
    "IDFCFIRSTB": {"vix": "headwind"},
    "FEDERALBNK": {"vix": "headwind"},
    "BANKBARODA": {"vix": "headwind", "nifty_bank": "tailwind"},
    "CANBK":      {"vix": "headwind"},
    "PNB":        {"vix": "headwind"},
    "HDFCLIFE":   {"vix": "headwind"},
    "SBILIFE":    {"vix": "headwind"},
    "ICICIPRULI": {"vix": "headwind"},
    "MCDOWELL-N": {},
    "CHOLAFIN":   {"vix": "headwind"},
    "MUTHOOTFIN": {"gold": "tailwind", "vix": "headwind"},
    "LICHSGFIN":  {"vix": "headwind"},
    "RECLTD":     {"vix": "headwind"},
    # Reliance — refining + retail + Jio; crude impact is complex
    "RELIANCE":   {"crude_brent": "tailwind"},
    # Consumer staples — palm oil / crude derivative input costs
    "HINDUNILVR": {"crude_brent": "headwind"},
    "NESTLEIND":  {},
    "BRITANNIA":  {},
    "DABUR":      {},
    "MARICO":     {"crude_brent": "headwind"},  # VAHO / edible oil input
    "GODREJCP":   {"crude_brent": "headwind"},
    "ITC":        {},
    "TATACONSUM": {},
    "UBL":        {},
    # Infra / Capital Goods
    "LT":         {},
    "SIEMENS":    {},
    "HAL":        {},
    "BHEL":       {"crude_brent": "neutral"},
    "CONCOR":     {},
    "MOTHERSON":  {"crude_brent": "headwind"},
    # Power / Utilities
    "POWERGRID":  {},
    "NTPC":       {"crude_brent": "neutral"},
    # E-commerce / New-age
    "ZOMATO":     {"vix": "headwind"},   # legacy — symbol now ETERNAL
    "ETERNAL":    {"vix": "headwind"},
    "PAYTM":      {"vix": "headwind", "nasdaq": "tailwind"},
    "NYKAA":      {"vix": "headwind"},
    "POLICYBZR":  {"vix": "headwind"},
    "DELHIVERY":  {"crude_brent": "headwind"},
    "IRCTC":      {"crude_brent": "headwind"},
    # Hotels / Consumer discretionary
    "INDHOTEL":   {},
    "OBEROIRLTY": {},
    # Pharma general
    "APOLLOHOSP": {},
    # Real estate
    "DLF":        {"vix": "headwind"},
    "DMART":      {},
    # Misc
    "TRENT":      {},
    "VOLTAS":     {"crude_brent": "headwind"},
    "HAVELLS":    {"crude_brent": "headwind"},
    "SUPREMEIND": {"crude_brent": "headwind"},  # plastics input
    "SRF":        {"crude_brent": "headwind"},
    "PIIND":      {"crude_brent": "headwind"},
    "ADANIENT":   {"crude_brent": "tailwind"},  # ports + energy complex
    "ADANIPORTS": {},
}


def _fetch_one(key: str, ticker_sym: str) -> tuple[str, float | None]:
    """Fetch latest 2-day close for a ticker and return % change."""
    try:
        df = yf.Ticker(ticker_sym).history(period="5d")
        if len(df) < 2:
            return key, None
        pct = (df["Close"].iloc[-1] - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100
        return key, round(pct, 2)
    except Exception:
        return key, None


def _fetch_level(key: str, ticker_sym: str) -> tuple[str, float | None]:
    """Fetch latest price level (for VIX, USD/INR, DXY — absolute levels matter)."""
    try:
        df = yf.Ticker(ticker_sym).history(period="5d")
        if df.empty:
            return key, None
        return key, round(float(df["Close"].iloc[-1]), 2)
    except Exception:
        return key, None


def _build_global_snapshot() -> dict:
    """Fetch all global tickers in parallel. Returns raw data snapshot."""
    pct_tickers = {k: v for k, v in GLOBAL_TICKERS.items()
                   if k not in ("vix", "india_vix", "usdinr", "dxy", "us10y")}
    level_tickers = {k: v for k, v in GLOBAL_TICKERS.items()
                     if k in ("vix", "india_vix", "usdinr", "dxy", "us10y")}

    changes: dict[str, float | None] = {}
    levels: dict[str, float | None] = {}

    with ThreadPoolExecutor(max_workers=10) as pool:
        pct_futures  = {pool.submit(_fetch_one,   k, v): k for k, v in pct_tickers.items()}
        level_futures = {pool.submit(_fetch_level, k, v): k for k, v in level_tickers.items()}

        for f in as_completed(pct_futures):
            k, val = f.result()
            changes[k] = val
        for f in as_completed(level_futures):
            k, val = f.result()
            levels[k] = val

    return {"changes": changes, "levels": levels, "fetched_at": time.time()}


def _score_global(snapshot: dict) -> dict:
    """
    Convert raw snapshot into a global sentiment score (0–100) and reasoning.

    Score interpretation: >55 = global tailwind, <45 = global headwind, 45-55 = neutral.
    """
    changes = snapshot["changes"]
    levels  = snapshot["levels"]

    score = 50
    reasons: list[dict] = []
    factors: dict[str, str] = {}  # factor_key -> "positive" | "negative" | "neutral"

    # ── US Equity Markets ────────────────────────────────────────────────────
    sp500_chg = changes.get("sp500")
    if sp500_chg is not None:
        if sp500_chg > 1.0:
            score += 10
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"S&P 500 up {sp500_chg:+.1f}% — strong US market tailwind for Indian equities"})
            factors["sp500"] = "positive"
        elif sp500_chg > 0.3:
            score += 5
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"S&P 500 up {sp500_chg:+.1f}% — mild US market support"})
            factors["sp500"] = "positive"
        elif sp500_chg < -1.0:
            score -= 10
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"S&P 500 down {sp500_chg:+.1f}% — US sell-off creates headwind for Indian markets"})
            factors["sp500"] = "negative"
        elif sp500_chg < -0.3:
            score -= 5
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"S&P 500 down {sp500_chg:+.1f}% — mild US weakness"})
            factors["sp500"] = "negative"
        else:
            factors["sp500"] = "neutral"

    nasdaq_chg = changes.get("nasdaq")
    if nasdaq_chg is not None:
        if nasdaq_chg > 1.5:
            score += 6
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"NASDAQ up {nasdaq_chg:+.1f}% — tech/IT sector momentum"})
            factors["nasdaq"] = "positive"
        elif nasdaq_chg < -1.5:
            score -= 6
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"NASDAQ down {nasdaq_chg:+.1f}% — tech sector pressure"})
            factors["nasdaq"] = "negative"
        else:
            factors["nasdaq"] = "neutral"

    # ── VIX (Fear Index) ─────────────────────────────────────────────────────
    vix = levels.get("vix")
    if vix is not None:
        if vix > 30:
            score -= 12
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"VIX at {vix:.1f} — extreme fear; FII outflows from emerging markets likely"})
            factors["vix"] = "negative"
        elif vix > 20:
            score -= 6
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"VIX elevated at {vix:.1f} — caution; risk-off sentiment"})
            factors["vix"] = "negative"
        elif vix < 13:
            score += 6
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"VIX low at {vix:.1f} — markets calm; risk-on environment"})
            factors["vix"] = "positive"
        else:
            factors["vix"] = "neutral"

    india_vix = levels.get("india_vix")
    if india_vix is not None:
        if india_vix > 20:
            score -= 8
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"India VIX at {india_vix:.1f} — domestic uncertainty elevated"})
        elif india_vix < 12:
            score += 4
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"India VIX at {india_vix:.1f} — domestic market stable and calm"})

    # ── Crude Oil ────────────────────────────────────────────────────────────
    crude_chg = changes.get("crude_brent")
    if crude_chg is not None:
        if crude_chg > 2.0:
            score -= 6
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"Brent crude up {crude_chg:+.1f}% — inflation & import bill pressure on India's CAD"})
            factors["crude_brent"] = "up"
        elif crude_chg > 0.5:
            score -= 2
            factors["crude_brent"] = "up"
            reasons.append({"indicator": "Global", "signal": "NEUTRAL",
                             "reason": f"Brent crude up {crude_chg:+.1f}% — mild oil price rise"})
        elif crude_chg < -2.0:
            score += 6
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"Brent crude down {crude_chg:+.1f}% — lower oil = lower inflation, better CAD for India"})
            factors["crude_brent"] = "down"
        elif crude_chg < -0.5:
            score += 2
            factors["crude_brent"] = "down"
        else:
            factors["crude_brent"] = "neutral"

    # ── USD/INR ──────────────────────────────────────────────────────────────
    usdinr = levels.get("usdinr")
    if usdinr is not None:
        if usdinr > 86:
            # Weak INR: good for IT/pharma exporters, bad for importers
            factors["usdinr"] = "high"  # high = weak INR
            reasons.append({"indicator": "Global", "signal": "NEUTRAL",
                             "reason": f"USD/INR at ₹{usdinr:.1f} — weak rupee; tailwind for IT/pharma exporters, headwind for importers"})
        elif usdinr < 82:
            factors["usdinr"] = "low"   # low = strong INR
            reasons.append({"indicator": "Global", "signal": "NEUTRAL",
                             "reason": f"USD/INR at ₹{usdinr:.1f} — strong rupee; positive for import-heavy sectors"})
        else:
            factors["usdinr"] = "neutral"

    # ── US Dollar Index ──────────────────────────────────────────────────────
    dxy = levels.get("dxy")
    if dxy is not None:
        if dxy > 105:
            score -= 5
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"DXY at {dxy:.1f} — strong dollar triggers EM capital outflows"})
            factors["dxy"] = "high"
        elif dxy < 98:
            score += 5
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"DXY at {dxy:.1f} — weak dollar attracts capital into emerging markets"})
            factors["dxy"] = "low"
        else:
            factors["dxy"] = "neutral"

    # ── US 10-Year Yield ─────────────────────────────────────────────────────
    us10y = levels.get("us10y")
    if us10y is not None:
        if us10y > 4.5:
            score -= 5
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"US 10Y yield at {us10y:.2f}% — high US rates compress EM equity valuations"})
        elif us10y < 3.5:
            score += 4
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"US 10Y yield at {us10y:.2f}% — falling US rates positive for EM flows"})

    # ── Asian Markets ────────────────────────────────────────────────────────
    nikkei_chg = changes.get("nikkei")
    hangseng_chg = changes.get("hangseng")
    if nikkei_chg is not None and hangseng_chg is not None:
        asia_avg = (nikkei_chg + hangseng_chg) / 2
        if asia_avg > 1.0:
            score += 5
            reasons.append({"indicator": "Global", "signal": "BULLISH",
                             "reason": f"Asian markets positive (Nikkei {nikkei_chg:+.1f}%, Hang Seng {hangseng_chg:+.1f}%) — regional risk-on mood"})
            factors["hangseng"] = "positive"
        elif asia_avg < -1.0:
            score -= 5
            reasons.append({"indicator": "Global", "signal": "BEARISH",
                             "reason": f"Asian sell-off (Nikkei {nikkei_chg:+.1f}%, Hang Seng {hangseng_chg:+.1f}%) — contagion risk"})
            factors["hangseng"] = "negative"
        else:
            factors["hangseng"] = "neutral"

    # ── Gold ─────────────────────────────────────────────────────────────────
    gold_chg = changes.get("gold")
    if gold_chg is not None:
        if gold_chg > 1.0:
            # Rising gold = risk-off / inflation hedge; negative for equities broadly
            score -= 3
            reasons.append({"indicator": "Global", "signal": "NEUTRAL",
                             "reason": f"Gold up {gold_chg:+.1f}% — safe-haven demand signals risk-off; positive for gold-linked stocks"})
            factors["gold"] = "up"
        elif gold_chg < -1.0:
            score += 3
            factors["gold"] = "down"
        else:
            factors["gold"] = "neutral"

    # ── Sector Indices ───────────────────────────────────────────────────────
    nifty_it_chg = changes.get("nifty_it")
    if nifty_it_chg is not None:
        factors["nifty_it"] = "positive" if nifty_it_chg > 0.5 else ("negative" if nifty_it_chg < -0.5 else "neutral")

    nifty_bank_chg = changes.get("nifty_bank")
    if nifty_bank_chg is not None:
        factors["nifty_bank"] = "positive" if nifty_bank_chg > 0.5 else ("negative" if nifty_bank_chg < -0.5 else "neutral")

    return {
        "score": max(0, min(100, score)),
        "factors": factors,
        "levels": levels,
        "changes": changes,
        "reasons": reasons,
    }


def get_global_context(symbol: str | None = None) -> dict:
    """
    Return global macro context, optionally personalised for a stock symbol.
    Cached for 15 minutes so all parallel predictions share one network round-trip.
    """
    global _cache, _cache_expiry

    with _lock:
        if _cache is None or time.time() > _cache_expiry:
            print("[global] Fetching global macro signals …")
            snapshot = _build_global_snapshot()
            _cache = _score_global(snapshot)
            _cache_expiry = time.time() + CACHE_TTL
            print(f"[global] Done — global score: {_cache['score']}/100")

    base = dict(_cache)

    if symbol is None:
        return base

    # ── Stock-specific macro impact ──────────────────────────────────────────
    sensitivity = STOCK_MACRO_SENSITIVITY.get(symbol.upper(), {})
    factors = base.get("factors", {})
    stock_reasons: list[dict] = []
    stock_score_adj = 0

    for macro_key, impact_direction in sensitivity.items():
        factor_state = factors.get(macro_key)
        if factor_state is None:
            continue

        if impact_direction == "tailwind":
            if factor_state in ("positive", "up"):
                stock_score_adj += 5
                stock_reasons.append({
                    "indicator": "Macro",
                    "signal": "BULLISH",
                    "reason": f"{_macro_label(macro_key)} is a tailwind for {symbol} — direct positive impact on revenues/margins"
                })
            elif factor_state in ("negative", "down"):
                stock_score_adj -= 3
                stock_reasons.append({
                    "indicator": "Macro",
                    "signal": "BEARISH",
                    "reason": f"{_macro_label(macro_key)} is moving against {symbol}'s usual tailwind"
                })
        elif impact_direction == "headwind":
            if factor_state in ("positive", "up"):
                stock_score_adj -= 5
                stock_reasons.append({
                    "indicator": "Macro",
                    "signal": "BEARISH",
                    "reason": f"{_macro_label(macro_key)} rise is a cost/margin headwind for {symbol}"
                })
            elif factor_state in ("negative", "down"):
                stock_score_adj += 4
                stock_reasons.append({
                    "indicator": "Macro",
                    "signal": "BULLISH",
                    "reason": f"{_macro_label(macro_key)} falling reduces cost pressure for {symbol}"
                })
        # "neutral" / "mixed" — no adjustment

    base["stock_score_adj"] = stock_score_adj
    base["stock_reasons"] = stock_reasons
    return base


def _macro_label(key: str) -> str:
    return {
        "sp500":       "S&P 500",
        "nasdaq":      "NASDAQ",
        "dow":         "Dow Jones",
        "vix":         "VIX (fear index)",
        "india_vix":   "India VIX",
        "crude_brent": "Brent crude oil",
        "gold":        "Gold",
        "usdinr":      "USD/INR",
        "dxy":         "US Dollar Index",
        "us10y":       "US 10Y yield",
        "nikkei":      "Nikkei 225",
        "hangseng":    "Hang Seng",
        "nifty_it":    "Nifty IT index",
        "nifty_bank":  "Nifty Bank index",
    }.get(key, key)
