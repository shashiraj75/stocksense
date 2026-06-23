"""
Professional-grade quality factor scoring for Indian stocks.

Covers 10 dimensions:
  1.  Earnings Revisions         — EPS surprise trend + analyst upgrade/downgrade momentum
  2.  Institutional Ownership    — % held, institutions count (proxy for smart-money confidence)
  3.  Institutional Flow Proxy   — Volume/price divergence, MFI, OBV trend (flow direction)
  4.  Relative Strength          — Stock return vs Nifty 50 (1M, 3M, 6M)
  5.  Sector Strength            — Sector index momentum; is the stock swimming with the tide?
  6.  Valuation                  — PEG ratio, EV/EBITDA, sector-relative PE, margin of safety
  7.  Risk Management            — Max drawdown, volatility percentile, Sharpe ratio, drawdown recovery
  8.  Corporate Actions          — Dividend consistency, buybacks, stock splits (capital discipline)
  9.  Liquidity/Microstructure   — Volume trend, avg daily turnover, market-cap liquidity tier
  10. Quality Metrics            — Piotroski F-Score (9-pt), ROIC, asset efficiency

All functions accept pre-fetched (ticker, df, info) to avoid redundant yfinance API calls.
Sector index data is cached for 15 minutes (shared across all stocks in a generation run).
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# ── Sector index cache ────────────────────────────────────────────────────────
_sector_lock  = threading.Lock()
_sector_cache: dict | None = None
_sector_expiry: float = 0
SECTOR_CACHE_TTL = 900  # 15 min

# ── Nifty 50 history cache (shared between relative_strength + sector fetch) ──
_nifty_lock: threading.Lock = threading.Lock()
_nifty_df: pd.DataFrame | None = None
_nifty_expiry: float = 0
_NIFTY_TTL = 900  # 15 min


def _get_nifty_history() -> pd.DataFrame:
    """Return cached Nifty 50 7-month history. Fetched at most once per 15 min."""
    global _nifty_df, _nifty_expiry
    with _nifty_lock:
        if _nifty_df is not None and time.time() < _nifty_expiry:
            return _nifty_df
        try:
            df = yf.Ticker("^NSEI").history(period="7mo")
            _nifty_df = df
            _nifty_expiry = time.time() + _NIFTY_TTL
            return df
        except Exception:
            return pd.DataFrame()


# ── S&P 500 history cache (US equivalent of the Nifty cache above) ───────────
_sp500_lock: threading.Lock = threading.Lock()
_sp500_df: pd.DataFrame | None = None
_sp500_expiry: float = 0


def _get_sp500_history() -> pd.DataFrame:
    """Return cached S&P 500 7-month history. Fetched at most once per 15 min."""
    global _sp500_df, _sp500_expiry
    with _sp500_lock:
        if _sp500_df is not None and time.time() < _sp500_expiry:
            return _sp500_df
        try:
            df = yf.Ticker("^GSPC").history(period="7mo")
            _sp500_df = df
            _sp500_expiry = time.time() + _NIFTY_TTL
            return df
        except Exception:
            return pd.DataFrame()


def _get_benchmark_history(market: str) -> tuple[pd.DataFrame, str]:
    """Return (history, display name) for the market's broad benchmark index."""
    if market == "US":
        return _get_sp500_history(), "S&P 500"
    return _get_nifty_history(), "Nifty 50"


# Nifty sector index tickers
SECTOR_INDICES = {
    "IT":       "^CNXIT",
    "Bank":     "^NSEBANK",
    "Pharma":   "^CNXPHARMA",
    "Auto":     "^CNXAUTO",
    "FMCG":     "^CNXFMCG",
    "Metal":    "^CNXMETAL",
    "Energy":   "^CNXENERGY",
    "Realty":   "^CNXREALTY",
    "Infra":    "^CNXINFRA",
    "Finance":  "NIFTY_FIN_SERVICE.NS",  # ^CNXFINANCE is delisted/404s on yfinance now
    "Nifty50":  "^NSEI",
}

# Keyword fallback for IN stocks outside the curated STOCK_SECTOR map —
# maps screener.in's free-text sector/industry strings onto the same bucket
# keys as SECTOR_INDICES above, so the momentum score still works for stocks
# we haven't manually curated. Checked in order; first match wins.
# Each entry is a regex with \b word boundaries — plain substring matching
# previously let "it services" match inside "credit services" (IRFC's actual
# industry), wrongly bucketing a financial company as "IT".
_SECTOR_KEYWORD_MAP: dict[str, list[str]] = {
    "IT":      [r"\binformation technology\b", r"\bcomputer\b", r"\bsoftware\b", r"\bit services\b"],
    "Bank":    [r"\bbank\b"],
    "Finance": [r"\bfinancial services\b", r"\bfinance\b", r"\bnbfc\b", r"\binsurance\b", r"\basset management\b", r"\bhousing finance\b", r"\bcredit\b"],
    "Pharma":  [r"\bpharma\b", r"\bhealthcare\b", r"\bhospital\b", r"\bbiotechnology\b"],
    "Auto":    [r"\bautomobile\b", r"\bauto\b", r"\btyres\b"],
    "FMCG":    [r"\bfmcg\b", r"\bconsumer staples\b", r"\bfood products\b", r"\bbeverages\b", r"\bpersonal care\b", r"\bhousehold\b"],
    "Metal":   [r"\bmetal\b", r"\bmining\b", r"\bsteel\b", r"\biron\b"],
    "Energy":  [r"\boil\b", r"\bgas\b", r"\bpetroleum\b", r"\bpower generation\b", r"\benergy\b", r"\belectric utilities\b"],
    "Realty":  [r"\brealty\b", r"\breal estate\b"],
    "Infra":   [r"\binfrastructure\b", r"\bconstruction\b", r"\bcement\b", r"\bcapital goods\b", r"\bengineering\b"],
}

# Map each Nifty 100 symbol to its sector index key
STOCK_SECTOR: dict[str, str] = {
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "MPHASIS": "IT", "PERSISTENT": "IT", "OFSS": "IT", "LTIM": "IT",
    # Banking
    "HDFCBANK": "Bank", "ICICIBANK": "Bank", "SBIN": "Bank", "KOTAKBANK": "Bank",
    "AXISBANK": "Bank", "INDUSINDBK": "Bank", "BANDHANBNK": "Bank",
    "IDFCFIRSTB": "Bank", "FEDERALBNK": "Bank", "BANKBARODA": "Bank",
    "CANBK": "Bank", "PNB": "Bank",
    # Finance/NBFC
    "BAJFINANCE": "Finance", "BAJAJFINSV": "Finance", "CHOLAFIN": "Finance",
    "MUTHOOTFIN": "Finance", "LICHSGFIN": "Finance", "RECLTD": "Finance",
    "HDFCLIFE": "Finance", "SBILIFE": "Finance", "ICICIPRULI": "Finance",
    # Pharma
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "TORNTPHARM": "Pharma",
    "APOLLOHOSP": "Pharma",
    # Auto
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "EICHERMOT": "Auto",
    "TVSMOTOR": "Auto", "HEROMOTOCO": "Auto", "BAJAJ-AUTO": "Auto", "MOTHERSON": "Auto",
    # FMCG / Consumer Staples
    "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG", "DABUR": "FMCG",
    "MARICO": "FMCG", "GODREJCP": "FMCG", "ITC": "FMCG", "TATACONSUM": "FMCG",
    "UBL": "FMCG", "MCDOWELL-N": "FMCG",
    # Metals & Mining
    "TATASTEEL": "Metal", "JSWSTEEL": "Metal", "SAIL": "Metal", "HINDALCO": "Metal",
    "VEDL": "Metal", "NMDC": "Metal",
    # Energy / Oil & Gas
    "ONGC": "Energy", "BPCL": "Energy", "IOC": "Energy", "GAIL": "Energy",
    "HINDPETRO": "Energy", "RELIANCE": "Energy", "NTPC": "Energy", "POWERGRID": "Energy",
    "COALINDIA": "Energy",
    # Realty
    "DLF": "Realty", "OBEROIRLTY": "Realty",
    # Infra / Capital Goods
    "LT": "Infra", "SIEMENS": "Infra", "HAL": "Infra", "BHEL": "Infra",
    "CONCOR": "Infra", "ADANIPORTS": "Infra",
    # Consumer Discretionary / Paints / Others
    "TITAN": "Consumer", "ASIANPAINT": "FMCG", "PIDILITIND": "FMCG",
    # New-age tech/consumer — NOT Finance; classified by primary business
    "NAUKRI": "IT", "ZOMATO": "Consumer", "ETERNAL": "Consumer", "PAYTM": "Finance",
    "NYKAA": "Consumer", "POLICYBZR": "Finance",
    "DELHIVERY": "Infra", "IRCTC": "Infra",
    "INDHOTEL": "Consumer", "TRENT": "Consumer", "VOLTAS": "FMCG", "HAVELLS": "FMCG",
    "ADANIENT": "Energy",
    "ADANIPOWER": "Energy", "ADANIGRE": "Energy",
    "DMART": "FMCG", "SUPREMEIND": "FMCG",
    "SRF": "Pharma", "PIIND": "Pharma", "ULTRACEMCO": "Infra", "SHREECEM": "Infra",
    "GRASIM": "Infra",
    # Additional Nifty 100 stocks
    "BAJAJHINDGE": "FMCG", "BOSCHLTD": "Auto", "CUMMINSIND": "Infra",
    "DIXON": "Consumer", "ESCORTS": "Auto", "FLUOROCHEM": "Pharma",
    "GMRINFRA": "Infra", "GODREJPROP": "Realty", "INDIGO": "Infra",
    "INDUSTOWER": "IT", "JSWENERGY": "Energy", "KALYANKJIL": "Consumer",
    "LICI": "Finance", "LODHA": "Realty", "MAXHEALTH": "Pharma",
    "PAGEIND": "Consumer",
    "PHOENIXLTD": "Realty", "PPLPHARMA": "Pharma", "SOLARINDS": "Pharma",
    "SUNTV": "Consumer", "TIINDIA": "Auto", "TORNTPOWER": "Energy",
    "VBL": "FMCG", "ZYDUSLIFE": "Pharma",
}


def _fetch_sector_one(sector_sym: tuple[str, str]) -> tuple[str, dict | None]:
    """Fetch returns for one sector index (called in thread pool)."""
    sector, sym = sector_sym
    try:
        df = yf.Ticker(sym).history(period="4mo")
        if len(df) < 20:
            return sector, None
        ret_1m = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100
        ret_3m = (df["Close"].iloc[-1] - df["Close"].iloc[-63]) / df["Close"].iloc[-63] * 100 if len(df) >= 63 else None
        # Also warm the broad-benchmark cache if this is that benchmark's ticker
        if sym == "^NSEI":
            global _nifty_df, _nifty_expiry
            with _nifty_lock:
                if _nifty_df is None or time.time() >= _nifty_expiry:
                    _nifty_df = yf.Ticker("^NSEI").history(period="7mo")
                    _nifty_expiry = time.time() + _NIFTY_TTL
        elif sym == "^GSPC":
            global _sp500_df, _sp500_expiry
            with _sp500_lock:
                if _sp500_df is None or time.time() >= _sp500_expiry:
                    _sp500_df = yf.Ticker("^GSPC").history(period="7mo")
                    _sp500_expiry = time.time() + _NIFTY_TTL
        return sector, {"1m": round(ret_1m, 2), "3m": round(ret_3m, 2) if ret_3m is not None else None}
    except Exception:
        return sector, None


def _get_sector_returns() -> dict[str, dict | None]:
    """Fetch 1M and 3M returns for all sector indices in parallel. Cached 15 min."""
    global _sector_cache, _sector_expiry
    with _sector_lock:
        if _sector_cache is not None and time.time() < _sector_expiry:
            return _sector_cache
        # Parallel fetch — was 11 sequential calls (~16s), now ~2s
        result: dict[str, dict | None] = {}
        with ThreadPoolExecutor(max_workers=len(SECTOR_INDICES)) as pool:
            for sector, val in pool.map(_fetch_sector_one, SECTOR_INDICES.items()):
                result[sector] = val
        _sector_cache = result
        _sector_expiry = time.time() + SECTOR_CACHE_TTL
        return result


# ── US sector strength (SPDR Select Sector ETFs as proxies) ──────────────────
# Maps yfinance's GICS `info["sector"]` string (verified directly against
# real tickers — AAPL/MSFT->Technology, JPM->Financial Services, XOM->Energy,
# JNJ->Healthcare, PG->Consumer Defensive, AMZN/HD->Consumer Cyclical,
# CAT->Industrials, LIN->Basic Materials, AMT->Real Estate, NEE->Utilities,
# VZ->Communication Services) to its SPDR sector ETF, so no static per-symbol
# mapping is needed the way STOCK_SECTOR is for the curated Nifty 100 list —
# every US stock with a populated `sector` field gets a real classification.
US_SECTOR_INDICES = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Healthcare":             "XLV",
    "Consumer Defensive":     "XLP",
    "Consumer Cyclical":      "XLY",
    "Energy":                 "XLE",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
    "SP500":                  "^GSPC",
}

_us_sector_lock  = threading.Lock()
_us_sector_cache: dict | None = None
_us_sector_expiry: float = 0


def _get_us_sector_returns() -> dict[str, dict | None]:
    """Fetch 1M/3M returns for all US sector ETFs + S&P 500. Cached 15 min."""
    global _us_sector_cache, _us_sector_expiry
    with _us_sector_lock:
        if _us_sector_cache is not None and time.time() < _us_sector_expiry:
            return _us_sector_cache
        result: dict[str, dict | None] = {}
        with ThreadPoolExecutor(max_workers=len(US_SECTOR_INDICES)) as pool:
            for sector, val in pool.map(_fetch_sector_one, US_SECTOR_INDICES.items()):
                result[sector] = val
        _us_sector_cache = result
        _us_sector_expiry = time.time() + SECTOR_CACHE_TTL
        return result


# ── 1. EARNINGS REVISIONS ────────────────────────────────────────────────────

def earnings_revision_score(ticker, info: dict) -> dict:
    """
    Score based on:
    - Recent EPS surprise trend (beating vs missing estimates)
    - Analyst recommendation upgrade/downgrade momentum
    """
    score = 50
    reasons: list[str] = []

    # EPS Surprise trend
    try:
        eh = ticker.earnings_history
        if eh is not None and not eh.empty and "surprisePercent" in eh.columns:
            recent = eh.dropna(subset=["surprisePercent"]).tail(4)
            if len(recent) >= 2:
                surprises = recent["surprisePercent"].tolist()
                beats = sum(1 for s in surprises if s > 0)
                avg_surprise = np.mean(surprises)

                if beats == 4:
                    score += 16
                    reasons.append(f"Beat EPS estimates all 4 consecutive quarters (avg +{avg_surprise:.1%})")
                elif beats == 3:
                    score += 10
                    reasons.append(f"Beat EPS estimates 3 of last 4 quarters (avg {avg_surprise:+.1%})")
                elif beats <= 1:
                    score -= 10
                    reasons.append(f"Missed EPS estimates {4-beats} of last 4 quarters (avg {avg_surprise:+.1%})")
                else:
                    reasons.append(f"Mixed EPS surprises — beat {beats}/4 quarters")

                # Trend: is accuracy improving or worsening?
                if len(surprises) >= 3:
                    if surprises[-1] > surprises[-2] > surprises[-3]:
                        score += 6
                        reasons.append("EPS surprise trend improving — estimates being beaten by wider margins")
                    elif surprises[-1] < surprises[-2] < surprises[-3]:
                        score -= 6
                        reasons.append("EPS surprise trend deteriorating — misses widening")
    except Exception:
        pass

    # Analyst recommendation momentum
    try:
        rec = ticker.recommendations
        if rec is not None and not rec.empty and "strongBuy" in rec.columns:
            rec = rec.sort_index()
            if len(rec) >= 2:
                curr = rec.iloc[-1]
                prev = rec.iloc[-2]
                curr_bull = int(curr.get("strongBuy", 0)) + int(curr.get("buy", 0))
                prev_bull = int(prev.get("strongBuy", 0)) + int(prev.get("buy", 0))
                curr_bear = int(curr.get("sell", 0)) + int(curr.get("strongSell", 0))
                prev_bear = int(prev.get("sell", 0)) + int(prev.get("strongSell", 0))

                if curr_bull > prev_bull + 1:
                    score += 8
                    reasons.append(f"Analyst upgrades accelerating — bullish count rose from {prev_bull} → {curr_bull} this month")
                elif curr_bull < prev_bull - 1:
                    score -= 6
                    reasons.append(f"Analyst downgrades — bullish count fell from {prev_bull} → {curr_bull}")
                if curr_bear > prev_bear + 1:
                    score -= 5
                    reasons.append(f"Sell-side turning cautious — bearish recommendations rising")
    except Exception:
        pass

    # Forward vs trailing PE gap (earnings revision proxy)
    trailing_pe = info.get("trailingPE")
    forward_pe  = info.get("forwardPE")
    if trailing_pe and forward_pe and trailing_pe > 0 and forward_pe > 0:
        pe_compression = (trailing_pe - forward_pe) / trailing_pe * 100
        if pe_compression > 15:
            score += 8
            reasons.append(f"Forward P/E ({forward_pe:.1f}) well below trailing ({trailing_pe:.1f}) — earnings expected to grow significantly")
        elif pe_compression > 5:
            score += 4
            reasons.append(f"Forward P/E ({forward_pe:.1f}) below trailing ({trailing_pe:.1f}) — positive earnings revision expectation")
        elif pe_compression < -15:
            score -= 8
            reasons.append(f"Forward P/E ({forward_pe:.1f}) above trailing ({trailing_pe:.1f}) — earnings expected to decline")

    return {"score": max(0, min(100, score)), "reasons": reasons}


# ── 2. INSTITUTIONAL OWNERSHIP ───────────────────────────────────────────────

def institutional_ownership_score(ticker, info: dict) -> dict:
    """
    Score based on institutional and insider ownership levels.
    High institutional ownership = smart-money validated.
    Rising institutions count = accumulation signal.
    """
    score = 50
    reasons: list[str] = []

    try:
        inst_pct  = info.get("heldPercentInstitutions", 0) or 0
        insider_pct = info.get("heldPercentInsiders", 0) or 0
        inst_count = None

        try:
            mh = ticker.major_holders
            if mh is not None and not mh.empty:
                count_row = mh[mh.index.str.lower().str.contains("count", na=False)]
                if not count_row.empty:
                    inst_count = int(count_row.iloc[0].iloc[-1])
        except Exception:
            pass

        if inst_pct > 0.50:
            score += 14
            reasons.append(f"High institutional ownership ({inst_pct:.1%}) — strong smart-money conviction")
        elif inst_pct > 0.30:
            score += 8
            reasons.append(f"Solid institutional ownership ({inst_pct:.1%}) — well-covered by funds")
        elif inst_pct > 0.15:
            score += 3
            reasons.append(f"Moderate institutional ownership ({inst_pct:.1%})")
        elif inst_pct < 0.05:
            score -= 5
            reasons.append(f"Very low institutional ownership ({inst_pct:.1%}) — limited institutional interest")

        if inst_count:
            if inst_count > 300:
                score += 6
                reasons.append(f"{inst_count} institutions hold this stock — broad institutional participation")
            elif inst_count > 100:
                score += 3
                reasons.append(f"{inst_count} institutions hold this stock")
            elif inst_count < 20:
                score -= 4
                reasons.append(f"Only {inst_count} institutional holders — niche / under-covered")

        # High insider ownership = promoter confidence (India context)
        if insider_pct > 0.60:
            score += 5
            reasons.append(f"High promoter/insider holding ({insider_pct:.1%}) — strong founder commitment")
        elif insider_pct > 0.50:
            score += 3
            reasons.append(f"Promoter holding {insider_pct:.1%} — majority owner aligned with shareholders")
        elif insider_pct < 0.20:
            score -= 3
            reasons.append(f"Low insider/promoter holding ({insider_pct:.1%}) — limited skin-in-the-game")

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons}


# ── 3. RELATIVE STRENGTH ─────────────────────────────────────────────────────

def relative_strength_score(df: pd.DataFrame, market: str = "IN") -> dict:
    """
    Compare stock's 1M, 3M, 6M returns vs its market's broad benchmark
    (Nifty 50 for IN, S&P 500 for US). Outperforming the benchmark is a
    strong quality signal.
    """
    score = 50
    reasons: list[str] = []

    try:
        benchmark_data, benchmark_name = _get_benchmark_history(market)  # shared 15-min cache — no duplicate fetch
        if benchmark_data.empty or len(df) < 21:
            return {"score": 50, "reasons": []}

        stock_close = df["Close"]
        bench_close = benchmark_data["Close"]

        periods = {"1M": 21, "3M": 63, "6M": 126}
        rs_scores = []

        for label, days in periods.items():
            if len(stock_close) >= days and len(bench_close) >= days:
                stock_ret = (stock_close.iloc[-1] - stock_close.iloc[-days]) / stock_close.iloc[-days] * 100
                bench_ret = (bench_close.iloc[-1] - bench_close.iloc[-days]) / bench_close.iloc[-days] * 100
                rs = stock_ret - bench_ret

                if rs > 10:
                    rs_scores.append(+12)
                    reasons.append(f"Outperforming {benchmark_name} by {rs:+.1f}% over {label} — strong relative strength")
                elif rs > 4:
                    rs_scores.append(+6)
                    reasons.append(f"Outperforming {benchmark_name} by {rs:+.1f}% over {label}")
                elif rs < -10:
                    rs_scores.append(-12)
                    reasons.append(f"Underperforming {benchmark_name} by {abs(rs):.1f}% over {label} — weak relative strength")
                elif rs < -4:
                    rs_scores.append(-6)
                    reasons.append(f"Slightly underperforming {benchmark_name} over {label} ({rs:+.1f}%)")
                else:
                    rs_scores.append(0)

        if rs_scores:
            avg_adj = np.mean(rs_scores)
            score = max(0, min(100, 50 + avg_adj))

    except Exception:
        pass

    return {"score": round(score), "reasons": reasons[:2]}  # top 2 most recent periods


# ── 4. SECTOR STRENGTH ───────────────────────────────────────────────────────

def sector_strength_score(symbol: str, info: dict | None = None, market: str = "IN") -> dict:
    """
    Check if the stock's sector is outperforming its market's broad benchmark.
    Sector momentum is a powerful short-to-medium term predictor.

    IN: sector comes from the curated STOCK_SECTOR map (Nifty 100-ish symbols),
        compared against Nifty sector indices vs Nifty 50.
    US: sector comes from yfinance's own GICS `info["sector"]` field (works for
        any US stock, not just a curated list), compared against SPDR Select
        Sector ETFs vs the S&P 500.
    """
    score = 50
    reasons: list[str] = []

    if market == "US":
        sector = (info or {}).get("sector")
        if not sector or sector not in US_SECTOR_INDICES:
            return {"score": 50, "reasons": [], "sector": sector or "Unknown"}
        returns = _get_us_sector_returns()
        benchmark_key = "SP500"
        benchmark_name = "S&P 500"
    else:
        sector = STOCK_SECTOR.get(symbol.upper())
        if sector is None:
            # Not in the curated ~150-stock map — fall back to the
            # screener.in-derived sector/industry text (now reliably populated
            # via augment_info_with_screener) instead of just saying "Unknown".
            # Try to map it onto one of our curated index buckets first so the
            # momentum score still works; otherwise show the real text as-is.
            raw = ((info or {}).get("industry") or (info or {}).get("sector") or "").lower()
            mapped = next((bucket for bucket, patterns in _SECTOR_KEYWORD_MAP.items()
                           if any(re.search(p, raw) for p in patterns)), None)
            if mapped is None:
                fallback = (info or {}).get("sector") or (info or {}).get("industry")
                return {"score": 50, "reasons": [], "sector": fallback or "Unknown"}
            sector = mapped
        returns = _get_sector_returns()
        benchmark_key = "Nifty50"
        benchmark_name = "Nifty 50"

    try:
        sector_data    = returns.get(sector)
        benchmark_data = returns.get(benchmark_key)

        if sector_data and benchmark_data:
            s1m = sector_data.get("1m", 0) or 0
            n1m = benchmark_data.get("1m", 0) or 0
            s3m = sector_data.get("3m", 0) or 0
            n3m = benchmark_data.get("3m", 0) or 0

            rel_1m = s1m - n1m
            rel_3m = s3m - n3m

            if rel_1m > 5 and rel_3m > 5:
                score += 16
                reasons.append(f"{sector} sector is strongly outperforming {benchmark_name} ({s1m:+.1f}% 1M, {s3m:+.1f}% 3M) — sector tailwind")
            elif rel_1m > 2 or rel_3m > 4:
                score += 8
                reasons.append(f"{sector} sector outperforming {benchmark_name} ({s1m:+.1f}% 1M) — positive sector rotation")
            elif rel_1m < -5 and rel_3m < -5:
                score -= 14
                reasons.append(f"{sector} sector underperforming {benchmark_name} ({s1m:+.1f}% 1M) — sector headwind; fighting the trend")
            elif rel_1m < -2 or rel_3m < -4:
                score -= 7
                reasons.append(f"{sector} sector slightly underperforming {benchmark_name} ({s1m:+.1f}% 1M)")
            else:
                reasons.append(f"{sector} sector inline with {benchmark_name} ({s1m:+.1f}% 1M)")

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons, "sector": sector}


# ── 5. CORPORATE ACTIONS ─────────────────────────────────────────────────────

def corporate_actions_score(ticker, info: dict) -> dict:
    """
    Score based on:
    - Dividend consistency and growth (capital discipline)
    - Stock buybacks (confidence in undervaluation)
    - Dilution risk (new share issuances are negative)
    - Stock splits (indicates strong performance history)
    """
    score = 50
    reasons: list[str] = []

    try:
        actions = ticker.actions
        dividends = ticker.dividends

        # Dividend track record
        if dividends is not None and len(dividends) > 0:
            divs = dividends.sort_index()
            years_of_dividends = len(divs)
            div_values = divs.values

            if years_of_dividends >= 5:
                score += 8
                reasons.append(f"Consistent dividend payer — {years_of_dividends} dividend payments on record")

                # Dividend growth trend
                if len(div_values) >= 3:
                    recent_3 = div_values[-3:]
                    if all(recent_3[i] <= recent_3[i+1] for i in range(len(recent_3)-1)):
                        growth = (recent_3[-1] - recent_3[0]) / recent_3[0] * 100 if recent_3[0] > 0 else 0
                        score += 6
                        reasons.append(f"Dividend growing consistently — {growth:.0f}% growth over recent payments")
                    elif recent_3[-1] < recent_3[-2]:
                        score -= 4
                        reasons.append("Recent dividend cut — signals potential earnings stress")

            elif years_of_dividends >= 2:
                score += 3
                reasons.append(f"Pays dividends ({years_of_dividends} payments on record)")

        # Payout ratio — too high = unsustainable
        payout = info.get("payoutRatio")
        if payout:
            if 0 < payout < 0.40:
                score += 4
                reasons.append(f"Healthy payout ratio ({payout:.0%}) — retaining sufficient earnings for growth")
            elif payout > 0.80:
                score -= 6
                reasons.append(f"High payout ratio ({payout:.0%}) — may not be sustainable")

        # Buybacks from cashflow
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                buyback_row = None
                for label in ["Repurchase Of Capital Stock", "Common Stock Repurchase", "Net Common Stock Issuance"]:
                    if label in cf.index:
                        buyback_row = cf.loc[label]
                        break
                if buyback_row is not None:
                    latest_buyback = buyback_row.dropna().iloc[0] if not buyback_row.dropna().empty else 0
                    if latest_buyback < -1e7:   # negative = cash outflow for buybacks
                        score += 8
                        reasons.append(f"Active share buyback programme — management confident in intrinsic value")

                # Dilution check — new stock issuance is negative
                issuance_row = None
                for label in ["Common Stock Issuance", "Issuance Of Capital Stock"]:
                    if label in cf.index:
                        issuance_row = cf.loc[label]
                        break
                if issuance_row is not None:
                    latest_issuance = issuance_row.dropna().iloc[0] if not issuance_row.dropna().empty else 0
                    if latest_issuance > 1e9:  # large equity raise
                        score -= 5
                        reasons.append("Significant equity dilution — new shares issued; EPS per share pressure")
        except Exception:
            pass

        # Stock splits (historical positive performance signal)
        if actions is not None and not actions.empty and "Stock Splits" in actions.columns:
            splits = actions[actions["Stock Splits"] > 0]
            if len(splits) > 0:
                score += 3
                reasons.append(f"Stock split history ({len(splits)} split(s)) — indicates strong long-term price performance")

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons}


# ── 6. LIQUIDITY / MICROSTRUCTURE ────────────────────────────────────────────

def liquidity_score(df: pd.DataFrame, info: dict) -> dict:
    """
    Score based on:
    - Average daily trading volume and turnover
    - Volume trend vs 20-day average
    - Market cap liquidity tier (Large/Mid/Small cap)
    - Beta (low beta = stability; extreme beta = higher risk)
    """
    score = 50
    reasons: list[str] = []

    try:
        avg_vol   = info.get("averageVolume") or 0
        avg_vol10 = info.get("averageVolume10days") or avg_vol
        mkt_cap   = info.get("marketCap") or 0
        beta      = info.get("beta")

        # Market cap tier
        if mkt_cap >= 2e12:           # ₹2T+ = Large cap
            score += 10
            reasons.append(f"Large-cap (₹{mkt_cap/1e12:.1f}T market cap) — high liquidity, institutional grade")
        elif mkt_cap >= 5e11:         # ₹500B+
            score += 6
            reasons.append(f"Mid-large cap (₹{mkt_cap/1e9:.0f}B) — good liquidity")
        elif mkt_cap >= 1e11:         # ₹100B+
            score += 2
            reasons.append(f"Mid-cap (₹{mkt_cap/1e9:.0f}B)")
        elif mkt_cap > 0 and mkt_cap < 1e10:  # <₹10B
            score -= 8
            reasons.append(f"Small/micro-cap (₹{mkt_cap/1e9:.1f}B) — liquidity risk; wide bid-ask spreads likely")

        # Average daily volume
        if avg_vol >= 5_000_000:
            score += 8
            reasons.append(f"Very high average daily volume ({avg_vol/1e6:.1f}M shares) — excellent liquidity")
        elif avg_vol >= 1_000_000:
            score += 4
            reasons.append(f"Good daily volume ({avg_vol/1e6:.1f}M shares)")
        elif avg_vol < 100_000:
            score -= 8
            reasons.append(f"Low daily volume ({avg_vol:,.0f} shares) — illiquid; impact cost on large orders")

        # Volume surge vs 20-day average (from OHLCV data)
        if "Volume" in df.columns and len(df) >= 21:
            recent_vol   = df["Volume"].iloc[-1]
            avg_vol_20d  = df["Volume"].iloc[-21:-1].mean()
            if avg_vol_20d > 0:
                vol_ratio = recent_vol / avg_vol_20d
                if vol_ratio > 2.0:
                    score += 6
                    reasons.append(f"Volume spike — today's volume {vol_ratio:.1f}x the 20-day average; high conviction activity")
                elif vol_ratio > 1.3:
                    score += 3
                    reasons.append(f"Above-average volume ({vol_ratio:.1f}x 20D avg) — accumulation likely")
                elif vol_ratio < 0.4:
                    score -= 4
                    reasons.append(f"Very low volume ({vol_ratio:.1f}x 20D avg) — low conviction")

        # Beta
        if beta is not None:
            if 0.5 <= beta <= 1.2:
                score += 4
                reasons.append(f"Beta {beta:.2f} — healthy risk profile; moves with market but not amplified")
            elif beta > 2.0:
                score -= 5
                reasons.append(f"High beta ({beta:.2f}) — amplified market moves; higher risk")
            elif beta < 0:
                score -= 3
                reasons.append(f"Negative beta ({beta:.2f}) — counter-cyclical; unusual behaviour")

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons}


# ── 7. QUALITY METRICS ───────────────────────────────────────────────────────

def quality_metrics_score(ticker, df: pd.DataFrame, info: dict) -> dict:
    """
    Composite quality score using:
    - Piotroski F-Score (9-point scale — classic value+quality filter)
    - ROIC (Return on Invested Capital — how efficiently capital is deployed)
    - Asset efficiency trend
    - Earnings quality (cash earnings vs accrual earnings)
    """
    score = 50
    reasons: list[str] = []

    # ── Piotroski F-Score ────────────────────────────────────────────────────
    f_score = 0
    f_score_available = False   # stays False if financials are absent
    f_details: list[str] = []
    try:
        fin = ticker.financials
        bs  = ticker.balance_sheet
        cf  = ticker.cashflow

        if fin is not None and not fin.empty and bs is not None and not bs.empty and cf is not None and not cf.empty:
            f_score_available = True
            # Sort columns oldest → newest
            fin_s = fin.sort_index(axis=1)
            bs_s  = bs.sort_index(axis=1)
            cf_s  = cf.sort_index(axis=1)

            def _get(df_s, *labels):
                for l in labels:
                    if l in df_s.index:
                        row = df_s.loc[l].dropna()
                        if not row.empty:
                            return row.sort_index().values
                return None

            # P1: ROA > 0
            roa = info.get("returnOnAssets")
            if roa and roa > 0:
                f_score += 1; f_details.append("ROA positive ✓")

            # P2: Operating Cash Flow > 0
            ocf = _get(cf_s, "Operating Cash Flow", "Cash From Operations")
            if ocf is not None and len(ocf) >= 1 and ocf[-1] > 0:
                f_score += 1; f_details.append("Operating CF positive ✓")

            # P3: Δ ROA positive (improving)
            net_inc = _get(fin_s, "Net Income", "Net Income Common Stockholders")
            total_assets_row = _get(bs_s, "Total Assets")
            if net_inc is not None and total_assets_row is not None and len(net_inc) >= 2 and len(total_assets_row) >= 2:
                roa_curr = net_inc[-1] / total_assets_row[-1] if total_assets_row[-1] != 0 else 0
                roa_prev = net_inc[-2] / total_assets_row[-2] if total_assets_row[-2] != 0 else 0
                if roa_curr > roa_prev:
                    f_score += 1; f_details.append("ROA improving ✓")

            # P4: Accruals — cash earnings > net income (quality of earnings)
            if ocf is not None and net_inc is not None and total_assets_row is not None:
                if len(ocf) >= 1 and len(net_inc) >= 1 and len(total_assets_row) >= 1:
                    if total_assets_row[-1] != 0 and ocf[-1] / total_assets_row[-1] > net_inc[-1] / total_assets_row[-1]:
                        f_score += 1; f_details.append("Cash earnings > Accrual earnings ✓")

            # P5: Δ Leverage — long-term debt ratio decreasing
            ltd = _get(bs_s, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
            if ltd is not None and total_assets_row is not None and len(ltd) >= 2 and len(total_assets_row) >= 2:
                lev_curr = ltd[-1] / total_assets_row[-1] if total_assets_row[-1] != 0 else 0
                lev_prev = ltd[-2] / total_assets_row[-2] if total_assets_row[-2] != 0 else 0
                if lev_curr < lev_prev:
                    f_score += 1; f_details.append("Leverage declining ✓")

            # P6: Δ Current Ratio — improving
            curr_assets = _get(bs_s, "Current Assets", "Total Current Assets")
            curr_liab   = _get(bs_s, "Current Liabilities", "Total Current Liabilities")
            if curr_assets is not None and curr_liab is not None and len(curr_assets) >= 2 and len(curr_liab) >= 2:
                cr_curr = curr_assets[-1] / curr_liab[-1] if curr_liab[-1] != 0 else 0
                cr_prev = curr_assets[-2] / curr_liab[-2] if curr_liab[-2] != 0 else 0
                if cr_curr > cr_prev:
                    f_score += 1; f_details.append("Current ratio improving ✓")

            # P7: No dilution — shares not increased
            shares = _get(bs_s, "Ordinary Shares Number", "Share Issued", "Diluted Average Shares")
            if shares is None:
                shares_fin = _get(fin_s, "Diluted Average Shares", "Basic Average Shares")
                shares = shares_fin
            if shares is not None and len(shares) >= 2:
                if shares[-1] <= shares[-2] * 1.01:   # allow 1% tolerance
                    f_score += 1; f_details.append("No share dilution ✓")

            # P8: Δ Gross Margin — improving
            gm_curr = info.get("grossMargins")
            rev = _get(fin_s, "Total Revenue", "Revenue")
            cogs = _get(fin_s, "Cost Of Revenue", "Reconciled Cost Of Revenue")
            if rev is not None and cogs is not None and len(rev) >= 2 and len(cogs) >= 2:
                gm_now  = (rev[-1] - cogs[-1]) / rev[-1] if rev[-1] != 0 else 0
                gm_prev = (rev[-2] - cogs[-2]) / rev[-2] if rev[-2] != 0 else 0
                if gm_now > gm_prev:
                    f_score += 1; f_details.append("Gross margin expanding ✓")
            elif gm_curr and gm_curr > 0.20:
                # Fallback: award point only when current gross margin actually exceeds 20%
                f_score += 1

            # P9: Δ Asset Turnover — improving
            if rev is not None and total_assets_row is not None and len(rev) >= 2 and len(total_assets_row) >= 2:
                at_curr = rev[-1] / total_assets_row[-1] if total_assets_row[-1] != 0 else 0
                at_prev = rev[-2] / total_assets_row[-2] if total_assets_row[-2] != 0 else 0
                if at_curr > at_prev:
                    f_score += 1; f_details.append("Asset turnover improving ✓")

    except Exception:
        pass

    # Score F-Score only when financial statements were actually available.
    # Missing data → neutral contribution, not worst-case 0.
    if not f_score_available:
        reasons.append("Piotroski score unavailable — financial statements not found")
    elif f_score >= 8:
        score += 20
        reasons.append(f"Excellent Piotroski F-Score {f_score}/9 — high-quality business on all financial health metrics")
    elif f_score >= 6:
        score += 10
        reasons.append(f"Strong Piotroski F-Score {f_score}/9 — financially healthy company")
    elif f_score >= 4:
        reasons.append(f"Moderate Piotroski F-Score {f_score}/9 — mixed financial signals")
    else:
        score -= 12
        reasons.append(f"Weak Piotroski F-Score {f_score}/9 — multiple financial health red flags")

    try:
        ticker_obj = ticker
        bs  = ticker_obj.balance_sheet
        fin = ticker_obj.financials

        if bs is not None and not bs.empty and fin is not None and not fin.empty:
            bs_s  = bs.sort_index(axis=1)
            fin_s = fin.sort_index(axis=1)

            # Invested Capital (yfinance often has this directly)
            inv_cap = None
            if "Invested Capital" in bs_s.index:
                ic_row = bs_s.loc["Invested Capital"].dropna()
                if not ic_row.empty:
                    inv_cap = float(ic_row.sort_index().values[-1])

            # EBIT
            ebit = None
            for label in ["EBIT", "Operating Income", "Operating Income Or Losses"]:
                if label in fin_s.index:
                    row = fin_s.loc[label].dropna()
                    if not row.empty:
                        ebit = float(row.sort_index().values[-1])
                        break

            if inv_cap and ebit and inv_cap > 0:
                tax_rate = info.get("effectiveTaxRate") or 0.25
                nopat = ebit * (1 - tax_rate)
                roic  = nopat / inv_cap * 100

                if roic > 20:
                    score += 14
                    reasons.append(f"ROIC {roic:.1f}% — exceptional capital efficiency; creates significant shareholder value")
                elif roic > 12:
                    score += 8
                    reasons.append(f"ROIC {roic:.1f}% — good capital efficiency; above cost-of-capital")
                elif roic > 6:
                    score += 2
                    reasons.append(f"ROIC {roic:.1f}% — adequate capital returns")
                else:
                    score -= 8
                    reasons.append(f"ROIC {roic:.1f}% — poor capital efficiency; destroying value vs cost of capital")

    except Exception:
        pass

    # ── Earnings Quality — Cash Conversion ──────────────────────────────────
    try:
        net_margin = info.get("profitMargins", 0) or 0
        op_margin  = info.get("operatingMargins", 0) or 0
        if op_margin > 0 and net_margin > 0:
            conversion_ratio = net_margin / op_margin
            if conversion_ratio > 0.7:
                score += 4
                reasons.append(f"Strong earnings quality — {conversion_ratio:.0%} of operating profit converts to net income")
            elif conversion_ratio < 0.3:
                score -= 4
                reasons.append(f"Earnings quality concern — only {conversion_ratio:.0%} of operating profit reaches net income")
    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons, "piotroski": f_score}


# ── Altman Z-Score ────────────────────────────────────────────────────────────

def altman_zscore_signal(info: dict) -> dict:
    """
    Altman Z-Score bankruptcy predictor (Altman 1968; modified Z' for emerging markets).

    US / non-financial:
        Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
        Safe > 2.99 | Grey 1.81-2.99 | Distress < 1.81

    India / emerging market (Altman 1995 Z'-Score):
        Z' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
        Safe > 2.6 | Grey 1.1-2.6 | Distress < 1.1

    X1 = Working Capital / Total Assets
    X2 = Retained Earnings / Total Assets
    X3 = EBIT / Total Assets
    X4 = Market Cap / Total Liabilities (or Book Equity / Total Liabilities for Z')
    X5 = Revenue / Total Assets (Z only)
    """
    score = 50
    reasons: list[str] = []

    try:
        total_assets     = info.get("totalAssets")
        working_capital  = info.get("workingCapital")
        retained_earn    = info.get("retainedEarnings")
        ebit             = info.get("ebit") or info.get("operatingIncome")
        market_cap       = info.get("marketCap")
        total_debt       = info.get("totalDebt") or 0
        total_revenue    = info.get("totalRevenue")
        book_equity      = info.get("bookValue") or info.get("stockholdersEquity")

        # Need at least assets + one more field to compute anything meaningful
        if not total_assets or total_assets <= 0:
            return {"score": 50, "reasons": [], "z_score": None, "z_zone": "unavailable"}

        # Working capital fallback
        if working_capital is None:
            curr_assets = info.get("currentAssets") or 0
            curr_liab   = info.get("currentLiabilities") or 0
            working_capital = curr_assets - curr_liab if (curr_assets or curr_liab) else None

        # Total liabilities proxy
        total_liab = total_debt or (total_assets - (book_equity or 0))
        if total_liab <= 0:
            total_liab = total_assets * 0.3  # rough fallback

        # Compute ratios — each defaults to 0 if data missing (conservative)
        x1 = (working_capital  / total_assets) if working_capital is not None else 0
        x2 = (retained_earn    / total_assets) if retained_earn   is not None else 0
        x3 = (ebit             / total_assets) if ebit            is not None else 0
        x4 = (market_cap       / total_liab)   if market_cap      is not None else 1.0
        x5 = (total_revenue    / total_assets) if total_revenue   is not None else 0

        # Detect Indian stock: use Z' model (no x5, different weights + thresholds)
        _exch = (info.get("exchange") or "").upper()
        is_india = (info.get("market") == "IN"
                    or _exch in ("NSE", "BSE", "NSI", "BOM")
                    or info.get("country") == "India")
        if is_india:
            z = 6.56*x1 + 3.26*x2 + 6.72*x3 + 1.05*x4
            safe_thresh, grey_thresh = 2.6, 1.1
            model = "Z'"
        else:
            z = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
            safe_thresh, grey_thresh = 2.99, 1.81
            model = "Z"

        z = round(z, 2)

        if z > safe_thresh:
            score += 12
            zone = "safe"
            reasons.append(f"Altman {model}-Score {z:.2f} — Safe Zone: strong balance sheet, low bankruptcy risk")
        elif z > grey_thresh:
            score += 0
            zone = "grey"
            reasons.append(f"Altman {model}-Score {z:.2f} — Grey Zone: moderate financial stress; monitor leverage")
        else:
            score -= 20
            zone = "distress"
            reasons.append(f"Altman {model}-Score {z:.2f} — Distress Zone: elevated bankruptcy risk; avoid")

        return {"score": max(0, min(100, score)), "reasons": reasons,
                "z_score": z, "z_zone": zone, "z_model": model}

    except Exception:
        return {"score": 50, "reasons": [], "z_score": None, "z_zone": "unavailable"}


# ── Sloan Accruals Ratio ──────────────────────────────────────────────────────

def sloan_accruals_signal(info: dict) -> dict:
    """
    Sloan (1996) Accruals Ratio: (Net Income - Operating CF) / Total Assets.

    High accruals = earnings driven by accounting entries, not real cash.
    Stocks in the top accruals quintile underperform by ~10% annually (Sloan 1996).

    Thresholds:
      < -5% : High cash earnings quality (CF >> Net Income) — bullish
      -5% to +5% : Neutral
      +5% to +10%: Mild earnings quality concern
      > +10% : Strong red flag — earnings likely overstated
    """
    score = 50
    reasons: list[str] = []

    try:
        net_income   = info.get("netIncome") or info.get("netIncomeToCommon")
        op_cf        = info.get("operatingCashflow") or info.get("operatingCashflows")
        total_assets = info.get("totalAssets")

        # Screener.in fallback for Indian stocks
        screener_d = info.get("_screener_data") or {}
        if op_cf is None and screener_d.get("operating_cf_latest_cr") is not None:
            op_cf = screener_d["operating_cf_latest_cr"] * 1e7  # Cr → ₹

        # Quarterly PAT as net income proxy for India
        if net_income is None:
            q_pat = screener_d.get("quarterly_pat_cr") or []
            if len(q_pat) >= 4:
                net_income = sum(q_pat[-4:]) * 1e7  # annualise last 4Q in ₹

        if net_income is None or op_cf is None or not total_assets or total_assets <= 0:
            return {"score": 50, "reasons": [], "accruals_ratio": None}

        accruals_ratio = (net_income - op_cf) / total_assets
        accruals_pct   = round(accruals_ratio * 100, 1)

        if accruals_ratio < -0.05:
            score += 10
            reasons.append(f"Low accruals ratio ({accruals_pct}%) — earnings are cash-backed; high quality")
        elif accruals_ratio <= 0.05:
            score += 3
            reasons.append(f"Neutral accruals ratio ({accruals_pct}%) — earnings quality acceptable")
        elif accruals_ratio <= 0.10:
            score -= 8
            reasons.append(f"Elevated accruals ratio ({accruals_pct}%) — portion of earnings not yet cash; verify quality")
        else:
            score -= 18
            reasons.append(f"High accruals ratio ({accruals_pct}%) — earnings significantly outpacing cash flow; manipulation risk")

        return {"score": max(0, min(100, score)), "reasons": reasons,
                "accruals_ratio": accruals_pct}

    except Exception:
        return {"score": 50, "reasons": [], "accruals_ratio": None}


# ── Buffett / Munger Quality Checklist ───────────────────────────────────────

def buffett_munger_score(info: dict, df: pd.DataFrame) -> dict:
    """
    8-point checklist distilled from Warren Buffett's shareholder letters and
    Charlie Munger's Poor Charlie's Almanack.

    1. Consistent earnings power  — 3Y/5Y profit CAGR > 10% (Buffett: predictable earnings)
    2. High ROE without leverage  — ROE > 15% with D/E < 100% (Buffett: return on equity)
    3. Capital efficiency (ROIC)  — ROCE > 15% (Munger: ROIC > WACC = moat)
    4. Earnings predictability    — low quarterly PAT variance (Munger: predictable = moatable)
    5. Positive owner earnings    — FCF > 0 or operating CF > 0 (Buffett: owner earnings)
    6. FCF yield > 4%             — FCF / Market Cap (Buffett's min acceptable return)
    7. Pricing power proxy        — gross margin > 25% stable (Munger: moat indicator)
    8. Management integrity       — zero/low pledge + no promoter selling (India-specific)
    """
    score    = 50
    reasons  : list[str] = []
    checklist: list[dict] = []  # surfaced to frontend as a visual checklist
    passed   = 0

    screener_d = info.get("_screener_data") or {}

    def _check(label: str, passed_: bool, bull_msg: str, bear_msg: str, weight: int = 1):
        nonlocal score, passed
        checklist.append({"criterion": label, "passed": passed_,
                          "note": bull_msg if passed_ else bear_msg})
        if passed_:
            score  += weight
            passed += 1
            reasons.append(bull_msg)
        else:
            reasons.append(bear_msg)

    # 1. Consistent earnings power
    pat_3y = screener_d.get("profit_growth_3y_pct")
    pat_5y = screener_d.get("profit_growth_5y_pct")
    eps_growth = info.get("earningsGrowth")
    growth_ok = (
        (pat_3y is not None and pat_3y > 10) or
        (pat_5y is not None and pat_5y > 8)  or
        (eps_growth is not None and eps_growth > 0.10)
    )
    _check("Consistent earnings growth",
           growth_ok,
           f"Earnings growing consistently ({pat_3y:.1f}% 3Y CAGR)" if pat_3y else "Earnings growth confirmed",
           f"Earnings growth weak/absent ({pat_3y:.1f}% 3Y CAGR)" if pat_3y else "No consistent earnings growth data",
           weight=8)

    # 2. High ROE without excessive leverage
    roe = info.get("returnOnEquity") or 0
    de  = info.get("debtToEquity")  or 0
    roe_ok = roe > 0.15 and de < 150  # D/E as % in yfinance convention
    _check("High ROE without leverage",
           roe_ok,
           f"ROE {roe*100:.1f}% with manageable debt (D/E {de:.0f}%) — Buffett quality",
           f"ROE {roe*100:.1f}% {'but high debt' if de >= 150 else '— below Buffett threshold (15%)'}",
           weight=8)

    # 3. Capital efficiency — ROCE > 15%
    roce = (info.get("returnOnCapitalEmployed") or 0) * 100
    roce_ok = roce > 15
    _check("Capital efficiency (ROCE > 15%)",
           roce_ok,
           f"ROCE {roce:.1f}% — capital deployed well above cost; Munger moat indicator",
           f"ROCE {roce:.1f}% — below 15%; capital allocation needs scrutiny",
           weight=7)

    # 4. Earnings predictability (low quarterly variance)
    q_pat = screener_d.get("quarterly_pat_cr") or []
    predictable = False
    cv_str = "N/A"
    if len(q_pat) >= 4:
        arr = [v for v in q_pat[-6:] if v is not None and v > 0]
        if len(arr) >= 4:
            mean_v = sum(arr) / len(arr)
            std_v  = (sum((x - mean_v)**2 for x in arr) / len(arr)) ** 0.5
            cv     = std_v / mean_v if mean_v > 0 else 99
            predictable = cv < 0.35
            cv_str = f"{cv:.2f}"
    _check("Predictable earnings (low variance)",
           predictable,
           f"Quarterly PAT variance low (CV {cv_str}) — predictable business; Munger loves this",
           f"Quarterly PAT volatile (CV {cv_str}) — earnings unpredictable; avoid",
           weight=6)

    # 5. Positive owner earnings (FCF / Operating CF)
    fcf    = info.get("freeCashflow")
    op_cf  = info.get("operatingCashflow") or info.get("operatingCashflows")
    if op_cf is None:
        op_cf_cr = screener_d.get("operating_cf_latest_cr")
        op_cf = op_cf_cr * 1e7 if op_cf_cr is not None else None
    cashflow_positive = (fcf is not None and fcf > 0) or (op_cf is not None and op_cf > 0)
    _check("Positive owner earnings (FCF > 0)",
           cashflow_positive,
           "Free/operating cash flow positive — real earnings, not accounting fiction",
           "Negative cash flow — earnings not converting to owner value; Buffett red flag",
           weight=7)

    # 6. FCF yield > 4% (Buffett's minimum acceptable return threshold)
    mkt_cap = info.get("marketCap")
    fcf_yield_ok = False
    fcf_yield_str = "N/A"
    if fcf and mkt_cap and mkt_cap > 0:
        fcf_yield = fcf / mkt_cap * 100
        fcf_yield_ok = fcf_yield > 4
        fcf_yield_str = f"{fcf_yield:.1f}%"
    _check("FCF yield > 4%",
           fcf_yield_ok,
           f"FCF yield {fcf_yield_str} — attractive cash return for equity holders",
           f"FCF yield {fcf_yield_str} — below Buffett's 4% minimum threshold",
           weight=6)

    # 7. Gross margin > 25% — pricing power / moat proxy (Munger)
    gm = info.get("grossMargins") or 0
    gm_ok = gm > 0.25
    _check("Gross margin > 25% (pricing power)",
           gm_ok,
           f"Gross margin {gm*100:.1f}% — strong pricing power; suggests durable moat",
           f"Gross margin {gm*100:.1f}% — thin margins; limited pricing power",
           weight=5)

    # 8. Management integrity (India: no pledge; elsewhere: low short interest)
    pledge = screener_d.get("promoter_pledge_pct")
    p_trend = screener_d.get("promoter_quarterly_pct") or []
    promoter_ok = True
    if pledge is not None:
        promoter_ok = pledge < 10
    short_ratio = info.get("shortRatio") or 0
    if pledge is None:
        promoter_ok = short_ratio < 5  # US: low short interest as integrity proxy
    p_drop = (p_trend[-1] - p_trend[-4]) if len(p_trend) >= 4 else 0
    integrity_ok = promoter_ok and p_drop >= -2
    _check("Management integrity / skin-in-the-game",
           integrity_ok,
           f"Low pledge ({pledge:.1f}%)" if pledge is not None else "No significant short interest — integrity signal",
           f"High pledge ({pledge:.1f}%) or promoter selling — Munger red flag" if pledge is not None else "High short interest — concern",
           weight=5)

    return {
        "score":     max(0, min(100, score)),
        "reasons":   reasons,
        "checklist": checklist,
        "passed":    passed,
        "total":     8,
    }


# ── Sector median PE benchmarks (calibrated Q2 2025) ────────────────────────
# Source: NSE sector index trailing P/E, Bloomberg consensus — review quarterly
SECTOR_MEDIAN_PE: dict[str, float] = {
    "IT":      32.0,   # premium for high-margin offshore earnings
    "Bank":    14.0,   # structurally lower; interest-rate sensitive
    "Finance": 22.0,   # NBFC/insurance at slight premium to banks
    "Pharma":  28.0,   # domestic pharma; adjusted down from 30 (US generic pressure)
    "Auto":    24.0,   # EV-transition premium lifted multiples
    "FMCG":    38.0,   # corrected from 48 (was overstated); FMCG now 35-42x realistic
    "Metal":    9.0,   # cyclical; China-demand dependent
    "Energy":  12.0,   # commodity / PSU discount
    "Realty":  40.0,   # post-RERA re-rating
    "Infra":   25.0,   # capex supercycle premium
    "Consumer": 35.0,  # new-age consumer / discretionary
}

INDIA_RISK_FREE_RATE = 0.068   # 10-year G-Sec yield (~6.8%)


# ── 3b. INSTITUTIONAL FLOW PROXY ─────────────────────────────────────────────

def institutional_flow_proxy(df: pd.DataFrame, fii_dii: dict | None = None) -> dict:
    """
    Estimate institutional BUYING vs SELLING direction using price-volume signals,
    blended with real NSE FII/DII daily flow data when available (Indian stocks only).

    1. OBV Trend (On-Balance Volume) — cumulative volume weighted by price direction.
    2. Money Flow Index (MFI) — volume-weighted RSI. >60 = buying pressure, <40 = selling.
    3. Price-Volume Divergence — accumulation vs distribution signature.
    4. NSE FII/DII actual net flows (Cr) — real institutional buy/sell data when provided.
    """
    score = 50
    reasons: list[str] = []

    try:
        if len(df) < 20:
            return {"score": 50, "reasons": []}

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # ── OBV Trend ────────────────────────────────────────────────────────
        obv = pd.Series(0.0, index=df.index)
        for i in range(1, len(df)):
            if close.iloc[i] > close.iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] + volume.iloc[i]
            elif close.iloc[i] < close.iloc[i-1]:
                obv.iloc[i] = obv.iloc[i-1] - volume.iloc[i]
            else:
                obv.iloc[i] = obv.iloc[i-1]

        # Use raw OBV difference — no normalization that could mislead near zero
        obv_20d_slope = float(obv.iloc[-1] - obv.iloc[-20])
        base_close = float(close.iloc[-20])
        price_20d_ret = (float(close.iloc[-1]) - base_close) / base_close if base_close != 0 else 0.0

        if obv_20d_slope > 0 and price_20d_ret > 0:
            score += 12
            reasons.append("OBV rising with price — volume confirms uptrend; institutional accumulation pattern")
        elif obv_20d_slope > 0 and price_20d_ret < 0:
            score += 8
            reasons.append("OBV rising while price dips — hidden buying; institutional accumulation despite surface weakness")
        elif obv_20d_slope < 0 and price_20d_ret > 0:
            score -= 10
            reasons.append("OBV falling while price rises — volume not confirming rally; potential distribution by institutions")
        elif obv_20d_slope < 0 and price_20d_ret < 0:
            score -= 8
            reasons.append("OBV declining with price — consistent selling pressure")

        # ── Money Flow Index (14-day) ─────────────────────────────────────────
        typical_price = (high + low + close) / 3
        raw_money_flow = typical_price * volume

        pos_flow = pd.Series(0.0, index=df.index)
        neg_flow = pd.Series(0.0, index=df.index)
        for i in range(1, len(df)):
            if typical_price.iloc[i] > typical_price.iloc[i-1]:
                pos_flow.iloc[i] = raw_money_flow.iloc[i]
            else:
                neg_flow.iloc[i] = raw_money_flow.iloc[i]

        period = 14
        pos_mf = pos_flow.rolling(period).sum()
        neg_mf = neg_flow.rolling(period).sum()
        # Standard MFI: 100 × positive_money_flow / total_money_flow
        mfi = 100 * pos_mf / (pos_mf + neg_mf + 1e-10)
        mfi_val = float(mfi.iloc[-1])

        if mfi_val > 70:
            score += 8
            reasons.append(f"MFI {mfi_val:.0f} — strong money inflow; institutions actively buying")
        elif mfi_val > 55:
            score += 4
            reasons.append(f"MFI {mfi_val:.0f} — positive money flow; mild institutional interest")
        elif mfi_val < 30:
            score -= 8
            reasons.append(f"MFI {mfi_val:.0f} — heavy money outflow; institutional selling pressure")
        elif mfi_val < 45:
            score -= 4
            reasons.append(f"MFI {mfi_val:.0f} — negative money flow; cautious institutional stance")

        # ── Volume Surge on Up Days (accumulation signature) ─────────────────
        if len(df) >= 10:
            up_days   = df[close > close.shift(1)].tail(10)
            down_days = df[close < close.shift(1)].tail(10)
            avg_up_vol   = up_days["Volume"].mean() if len(up_days) > 0 else 0
            avg_down_vol = down_days["Volume"].mean() if len(down_days) > 0 else 1
            vol_ratio = avg_up_vol / avg_down_vol if avg_down_vol > 0 else 1

            if vol_ratio > 1.5:
                score += 6
                reasons.append(f"Up-day volume {vol_ratio:.1f}x down-day volume — institutional accumulation pattern (buying dips, not chasing)")
            elif vol_ratio < 0.7:
                score -= 5
                reasons.append(f"Down-day volume {1/vol_ratio:.1f}x up-day volume — distribution pattern; selling on strength")

    except Exception:
        pass

    # ── Blend real NSE FII/DII flows when available (Indian stocks) ───────────
    if fii_dii and fii_dii.get("available"):
        fii_score = fii_dii["score"]  # 0-100, 50=neutral
        # Weight real data at 40%, proxy signals at 60%
        score = round(score * 0.6 + fii_score * 0.4)
        for r in fii_dii.get("reasons", [])[:2]:
            reasons.append(r)

    return {"score": max(0, min(100, score)), "reasons": reasons[:4]}


# ── 8. VALUATION ─────────────────────────────────────────────────────────────

def valuation_score(symbol: str, df: pd.DataFrame, info: dict) -> dict:
    """
    Multi-dimensional valuation assessment:
    1. PEG Ratio         — PE relative to earnings growth (≤1 = undervalued, >2 = expensive)
    2. EV/EBITDA         — Enterprise value multiple (cross-sector comparable)
    3. Sector-relative PE— Is the stock cheap or expensive vs sector median?
    4. Price-to-Book     — Book value anchor, especially for banks/financials
    5. Margin of Safety  — Current price vs analyst mean target (upside buffer)
    """
    score = 50
    reasons: list[str] = []

    try:
        pe          = info.get("trailingPE")
        forward_pe  = info.get("forwardPE")
        pb          = info.get("priceToBook")
        ev_ebitda   = info.get("enterpriseToEbitda")
        eps_growth  = info.get("earningsGrowth") or info.get("revenueGrowth")
        target_mean = info.get("targetMeanPrice")
        current     = info.get("currentPrice") or info.get("regularMarketPrice")

        # ── PEG Ratio ────────────────────────────────────────────────────────
        if pe and eps_growth and eps_growth > 0:
            peg = pe / (eps_growth * 100)   # eps_growth is decimal (0.20 = 20%)
            if peg < 0.75:
                score += 16
                reasons.append(f"PEG ratio {peg:.2f} — significantly undervalued relative to growth (PEG <1 is classic value signal)")
            elif peg < 1.0:
                score += 10
                reasons.append(f"PEG ratio {peg:.2f} — attractively valued relative to earnings growth")
            elif peg < 1.5:
                score += 4
                reasons.append(f"PEG ratio {peg:.2f} — fairly valued for the growth rate")
            elif peg < 2.5:
                score -= 4
                reasons.append(f"PEG ratio {peg:.2f} — moderately expensive relative to growth")
            else:
                score -= 12
                reasons.append(f"PEG ratio {peg:.2f} — expensive; growth priced in and more")

        # ── EV/EBITDA ────────────────────────────────────────────────────────
        if ev_ebitda and ev_ebitda > 0:
            sector = STOCK_SECTOR.get(symbol.upper(), "")
            # Sector-specific EV/EBITDA benchmarks
            ev_benchmarks = {
                "IT": 22, "Pharma": 18, "FMCG": 32, "Bank": 8,
                "Finance": 14, "Auto": 13, "Metal": 7, "Energy": 8,
                "Realty": 22, "Infra": 16, "Consumer": 28,
            }
            benchmark = ev_benchmarks.get(sector, 14)
            if benchmark == 0:
                benchmark = 14
            discount = (benchmark - ev_ebitda) / benchmark * 100

            if discount > 25:
                score += 10
                reasons.append(f"EV/EBITDA {ev_ebitda:.1f}x — trading at {discount:.0f}% discount to sector benchmark ({benchmark}x)")
            elif discount > 10:
                score += 5
                reasons.append(f"EV/EBITDA {ev_ebitda:.1f}x — below sector benchmark ({benchmark}x); reasonable valuation")
            elif discount < -30:
                score -= 10
                reasons.append(f"EV/EBITDA {ev_ebitda:.1f}x — {abs(discount):.0f}% premium to sector ({benchmark}x); priced for perfection")
            elif discount < -15:
                score -= 5
                reasons.append(f"EV/EBITDA {ev_ebitda:.1f}x — above sector benchmark ({benchmark}x)")

        # ── Sector-Relative PE ───────────────────────────────────────────────
        use_pe = forward_pe or pe
        sector = STOCK_SECTOR.get(symbol.upper(), "")
        sector_pe = SECTOR_MEDIAN_PE.get(sector)
        if use_pe and sector_pe and use_pe > 0 and sector_pe > 0:
            pe_discount = (sector_pe - use_pe) / sector_pe * 100
            if pe_discount > 30:
                score += 12
                reasons.append(f"Trades at {pe_discount:.0f}% discount to {sector} sector median PE ({sector_pe}x) — significant margin of safety")
            elif pe_discount > 15:
                score += 6
                reasons.append(f"PE {use_pe:.1f}x is {pe_discount:.0f}% below {sector} sector median ({sector_pe}x) — relatively cheap")
            elif pe_discount < -30:
                score -= 10
                reasons.append(f"PE {use_pe:.1f}x is {abs(pe_discount):.0f}% above {sector} sector median ({sector_pe}x) — premium valuation requires strong execution")
            elif pe_discount < -15:
                score -= 5
                reasons.append(f"PE {use_pe:.1f}x slightly above {sector} sector median ({sector_pe}x)")

        # ── Price-to-Book ────────────────────────────────────────────────────
        if pb and pb > 0:
            sector = STOCK_SECTOR.get(symbol.upper(), "")
            if sector in ("Bank", "Finance"):
                # For banks, P/B is the primary valuation anchor
                if pb < 1.0:
                    score += 12
                    reasons.append(f"P/B {pb:.2f}x — below book value; deeply undervalued for a bank/NBFC")
                elif pb < 2.0:
                    score += 6
                    reasons.append(f"P/B {pb:.2f}x — reasonable for a bank/NBFC")
                elif pb > 4.0:
                    score -= 8
                    reasons.append(f"P/B {pb:.2f}x — premium to book; high expectations baked in")
            else:
                if pb < 1.5:
                    score += 6
                    reasons.append(f"P/B {pb:.2f}x — trading near book value; strong asset backing")
                elif pb > 8.0:
                    score -= 5
                    reasons.append(f"P/B {pb:.2f}x — high price-to-book; limited asset margin of safety")

        # ── Margin of Safety (analyst target buffer) ─────────────────────────
        if target_mean and current and current > 0:
            upside = (target_mean - current) / current * 100
            if upside > 30:
                score += 10
                reasons.append(f"Analyst consensus target implies {upside:.0f}% upside — wide margin of safety")
            elif upside > 15:
                score += 5
                reasons.append(f"Analyst consensus target implies {upside:.0f}% upside")
            elif upside < -10:
                score -= 8
                reasons.append(f"Stock trading above analyst consensus target by {abs(upside):.0f}% — limited upside")

        # ── FCF Yield ────────────────────────────────────────────────────────
        fcf     = info.get("freeCashflow")
        mkt_cap = info.get("marketCap")
        if fcf is not None and mkt_cap and mkt_cap > 0:
            fcf_yield = fcf / mkt_cap * 100
            if fcf_yield > 5:
                score += 10
                reasons.append(f"FCF yield {fcf_yield:.1f}% — strong free cash generation relative to market cap; self-funding growth")
            elif fcf_yield > 3:
                score += 5
                reasons.append(f"FCF yield {fcf_yield:.1f}% — healthy free cash flow generation")
            elif fcf_yield > 0:
                score += 2
                reasons.append(f"FCF yield {fcf_yield:.1f}% — positive but modest free cash flow")
            elif fcf_yield < 0:
                score -= 8
                reasons.append(f"Negative FCF yield ({fcf_yield:.1f}%) — burning cash; reliant on external financing")

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons}


# ── 9. RISK MANAGEMENT ───────────────────────────────────────────────────────

def risk_management_score(df: pd.DataFrame, info: dict) -> dict:
    """
    Quantitative risk assessment:
    1. Max Drawdown          — Worst peak-to-trough decline (12M). High drawdown = fragile.
    2. Drawdown Recovery     — Has it recovered after the last major drawdown?
    3. Volatility Percentile — Where does current volatility sit vs its own history?
       Low vol = trending cleanly; high vol = noisy/risky.
    4. Sharpe Ratio (approx) — (Annualised return - risk-free rate) / annualised vol.
       >1 = good risk-adjusted return; <0 = not worth the risk.
    5. Downside Deviation    — Sortino-style; only counts negative return days.
    """
    score = 50
    reasons: list[str] = []

    try:
        if len(df) < 60:
            return {"score": 50, "reasons": []}

        close   = df["Close"]
        returns = close.pct_change().dropna()

        # ── Max Drawdown (12 months) ──────────────────────────────────────────
        lookback = min(252, len(close))
        recent   = close.iloc[-lookback:]
        peak     = recent.cummax()
        drawdown = (recent - peak) / peak * 100   # negative values
        max_dd   = float(drawdown.min())           # most negative = worst

        if max_dd > -10:
            score += 14
            reasons.append(f"Max drawdown only {max_dd:.1f}% over 12M — resilient stock; low capital destruction risk")
        elif max_dd > -20:
            score += 6
            reasons.append(f"Max drawdown {max_dd:.1f}% over 12M — manageable downside")
        elif max_dd < -40:
            score -= 14
            reasons.append(f"Max drawdown {max_dd:.1f}% over 12M — history of severe capital destruction; high risk")
        elif max_dd < -30:
            score -= 8
            reasons.append(f"Max drawdown {max_dd:.1f}% over 12M — significant historical drawdown")

        # ── Drawdown Recovery ─────────────────────────────────────────────────
        current_dd = float(drawdown.iloc[-1])
        if current_dd > -5:
            score += 6
            reasons.append("Currently near 12M high — full recovery from drawdowns; strong upward trend")
        elif current_dd < -25:
            score -= 6
            reasons.append(f"Currently {current_dd:.1f}% below 12M peak — still recovering from drawdown")

        # ── Volatility Percentile (30D vol vs 1Y history) ────────────────────
        if len(returns) >= 252:
            vol_30d  = returns.iloc[-30:].std() * (252 ** 0.5) * 100
            vol_hist = [returns.iloc[max(0,i-30):i].std() * (252**0.5) * 100
                        for i in range(30, len(returns), 5)]
            if vol_hist:
                vol_pct = sum(1 for v in vol_hist if v < vol_30d) / len(vol_hist) * 100
                if vol_pct < 25:
                    score += 10
                    reasons.append(f"Current volatility ({vol_30d:.1f}% ann.) in bottom quartile of its own history — unusually calm; trend likely to continue")
                elif vol_pct < 50:
                    score += 4
                    reasons.append(f"Current volatility ({vol_30d:.1f}% ann.) below historical median — controlled risk environment")
                elif vol_pct > 80:
                    score -= 10
                    reasons.append(f"Current volatility ({vol_30d:.1f}% ann.) in top quintile of its own history — elevated risk; wider stops needed")
                elif vol_pct > 65:
                    score -= 5
                    reasons.append(f"Current volatility ({vol_30d:.1f}% ann.) above historical median — choppier than usual")

        # ── Sharpe Ratio (1-year, annualised) ────────────────────────────────
        if len(returns) >= 252:
            ann_return = float((1 + returns.iloc[-252:].mean()) ** 252 - 1) * 100
            ann_vol    = float(returns.iloc[-252:].std() * (252 ** 0.5) * 100)
            if ann_vol > 0:
                sharpe = (ann_return - INDIA_RISK_FREE_RATE * 100) / ann_vol
                if sharpe > 1.5:
                    score += 12
                    reasons.append(f"Sharpe ratio {sharpe:.2f} — excellent risk-adjusted returns; high reward per unit of risk taken")
                elif sharpe > 1.0:
                    score += 6
                    reasons.append(f"Sharpe ratio {sharpe:.2f} — good risk-adjusted returns")
                elif sharpe > 0.5:
                    score += 2
                    reasons.append(f"Sharpe ratio {sharpe:.2f} — acceptable risk-adjusted returns")
                elif sharpe < 0:
                    score -= 10
                    reasons.append(f"Sharpe ratio {sharpe:.2f} — negative; not compensating for risk taken vs risk-free rate")
                elif sharpe < 0.3:
                    score -= 4
                    reasons.append(f"Sharpe ratio {sharpe:.2f} — below-average risk compensation")

        # ── Downside Deviation (Sortino-style) ───────────────────────────────
        if len(returns) >= 60:
            neg_returns = returns[returns < 0].iloc[-60:]
            if len(neg_returns) > 5:
                downside_dev = float(neg_returns.std() * (252 ** 0.5) * 100)
                if downside_dev < 10:
                    score += 6
                    reasons.append(f"Low downside deviation ({downside_dev:.1f}%) — losses are small and controlled")
                elif downside_dev > 30:
                    score -= 6
                    reasons.append(f"High downside deviation ({downside_dev:.1f}%) — losses can be sharp when they occur")

        # ── Earnings Volatility (EPS consistency across quarters) ─────────────
        # Proxy: measure cross-quarter consistency of 63-day rolling returns std.
        # High inconsistency → earnings are unpredictable → risk deduction.
        if len(returns) >= 126:
            try:
                window_vols = [
                    float(returns.iloc[max(0, i - 63):i].std() * (252 ** 0.5) * 100)
                    for i in range(63, len(returns), 21)
                ]
                if len(window_vols) >= 4:
                    mean_vol = np.mean(window_vols)
                    std_vol  = np.std(window_vols)
                    cv = std_vol / (mean_vol + 1e-10)   # coefficient of variation
                    if cv < 0.20:
                        score += 6
                        reasons.append("Highly consistent return volatility across quarters — predictable, stable stock behaviour")
                    elif cv < 0.35:
                        score += 2
                        reasons.append("Reasonably consistent volatility across quarters — low earnings surprise risk")
                    elif cv > 0.60:
                        score -= 8
                        reasons.append(f"Highly erratic return volatility (CV {cv:.2f}) — earnings and price moves are unpredictable")
                    elif cv > 0.45:
                        score -= 4
                        reasons.append(f"Above-average volatility inconsistency (CV {cv:.2f}) — stock can be choppy; execution risk elevated")
            except Exception:
                pass

    except Exception:
        pass

    return {"score": max(0, min(100, score)), "reasons": reasons[:5]}


# ── MASTER FUNCTION ──────────────────────────────────────────────────────────

def compute_all_quality_factors(symbol: str, ticker, df: pd.DataFrame, info: dict, horizon: str, market: str = "IN") -> dict:
    """
    Run all 10 quality factor checks and return a combined score + all reasons.

    Horizon-aware:
    - Short  : fast signals only (no deep Piotroski/ROIC to keep latency <30s)
    - Medium : adds corporate actions + quality metrics
    - Long   : full suite

    `market` ("IN"/"US") drives which benchmark/sector data relative_strength
    and sector_strength compare against — defaults to "IN" so any existing
    caller that omits it keeps the original Nifty-50-relative behavior.
    """
    results: dict[str, dict] = {}
    all_reasons: list[dict] = []

    # ── Always run (fast — from info dict + precomputed df) ──────────────────
    results["earnings_revision"]    = earnings_revision_score(ticker, info)
    results["institutional"]        = institutional_ownership_score(ticker, info)

    # Fetch real NSE FII/DII flows for Indian stocks (market-wide signal, cached 30 min)
    fii_dii_data: dict | None = None
    yf_market = (info.get("market") or info.get("exchange") or "").upper()
    is_indian = "NS" in yf_market or yf_market in ("NSE", "BSE", "IN")
    if is_indian:
        try:
            from services.nse_fii_dii import get_fii_dii_flow
            fii_dii_data = get_fii_dii_flow()
        except Exception:
            pass

    results["inst_flow"]            = institutional_flow_proxy(df, fii_dii=fii_dii_data)
    results["relative_strength"]    = relative_strength_score(df, market)
    results["sector_strength"]      = sector_strength_score(symbol, info, market)
    results["valuation"]            = valuation_score(symbol, df, info)
    results["risk_management"]      = risk_management_score(df, info)
    results["liquidity"]            = liquidity_score(df, info)

    # ── MF / institutional holding trend (India only) ─────────────────────────
    # Quarterly DII/FII trend from screener.in shareholding history.
    # Available for all Indian stocks — no extra API call needed.
    screener_d = info.get("_screener_data") or {}
    if is_indian and screener_d.get("dii_quarterly_pct"):
        try:
            from services.mf_holdings import compute_mf_signal
            results["mf_trend"] = compute_mf_signal(screener_d)
        except Exception:
            results["mf_trend"] = {"score": 50, "reasons": []}
    else:
        results["mf_trend"] = {"score": 50, "reasons": []}

    # ── Deeper analysis for medium/long only ─────────────────────────────────
    if horizon in ("medium", "long"):
        results["corporate_actions"] = corporate_actions_score(ticker, info)
        results["quality_metrics"]   = quality_metrics_score(ticker, df, info)
    else:
        results["corporate_actions"] = corporate_actions_score(ticker, info)   # fast
        results["quality_metrics"]   = {"score": 50, "reasons": [], "piotroski": None}

    # ── Academic signal layer (all horizons — these are hard filters not just scores) ──
    results["altman"]   = altman_zscore_signal(info)
    results["accruals"] = sloan_accruals_signal(info)
    results["buffett"]  = buffett_munger_score(info, df)

    # ── Horizon-adjusted weights ──────────────────────────────────────────────
    # Short term: momentum (RS, inst flow, sector) matters most
    # Long term:  valuation + quality metrics dominate
    # Altman distress = hard penalty regardless of horizon
    altman_zone = results["altman"].get("z_zone", "unavailable")
    if altman_zone == "distress":
        for k in results:
            if k not in ("altman",):
                results[k]["score"] = max(0, results[k].get("score", 50) - 15)

    if horizon == "short":
        weights = {
            "earnings_revision": 0.12,
            "institutional":     0.05,
            "mf_trend":          0.05,
            "inst_flow":         0.12,
            "relative_strength": 0.14,
            "sector_strength":   0.14,
            "valuation":         0.07,
            "risk_management":   0.09,
            "liquidity":         0.07,
            "corporate_actions": 0.03,
            "quality_metrics":   0.04,
            "altman":            0.03,  # distress zone is a short-term concern too
            "accruals":          0.02,
            "buffett":           0.03,
        }
    elif horizon == "medium":
        weights = {
            "earnings_revision": 0.12,
            "institutional":     0.05,
            "mf_trend":          0.07,
            "inst_flow":         0.08,
            "relative_strength": 0.10,
            "sector_strength":   0.10,
            "valuation":         0.11,
            "risk_management":   0.09,
            "liquidity":         0.05,
            "corporate_actions": 0.05,
            "quality_metrics":   0.06,
            "altman":            0.04,
            "accruals":          0.04,
            "buffett":           0.04,
        }
    else:  # long
        weights = {
            "earnings_revision": 0.10,
            "institutional":     0.05,
            "mf_trend":          0.08,
            "inst_flow":         0.04,
            "relative_strength": 0.06,
            "sector_strength":   0.06,
            "valuation":         0.13,
            "risk_management":   0.08,
            "liquidity":         0.03,
            "corporate_actions": 0.08,
            "quality_metrics":   0.10,
            "altman":            0.06,  # balance sheet health matters most long-term
            "accruals":          0.06,  # earnings quality critical for long-term
            "buffett":           0.07,  # Buffett/Munger = long-term compounding lens
        }

    combined = sum(results[k]["score"] * weights[k] for k in weights if k in results)

    # ── Collect reasoning tagged by dimension ─────────────────────────────────
    dimension_labels = {
        "earnings_revision": "Earnings",
        "institutional":     "Ownership",
        "mf_trend":          "MF Trend",
        "inst_flow":         "Inst. Flow",
        "relative_strength": "Rel. Strength",
        "sector_strength":   "Sector",
        "valuation":         "Valuation",
        "risk_management":   "Risk",
        "liquidity":         "Liquidity",
        "corporate_actions": "Corp. Actions",
        "quality_metrics":   "Quality",
        "altman":            "Balance Sheet",
        "accruals":          "Earnings Quality",
        "buffett":           "Buffett/Munger",
    }
    for key, label in dimension_labels.items():
        dim_score = results.get(key, {}).get("score", 50)
        for reason in results.get(key, {}).get("reasons", []):
            all_reasons.append({
                "indicator": label,
                "signal": "BUY" if dim_score >= 60 else ("BEARISH" if dim_score <= 40 else "INFO"),
                "reason": reason,
            })

    sector    = results.get("sector_strength", {}).get("sector", "Unknown")
    piotroski = results.get("quality_metrics", {}).get("piotroski")

    return {
        "score":           round(max(0, min(100, combined))),
        "breakdown":       results,
        "reasons":         all_reasons,
        "sector":          sector,
        "piotroski":       piotroski,
        # Academic signal summary — surfaced directly to frontend
        "altman_z":        results["altman"].get("z_score"),
        "altman_zone":     results["altman"].get("z_zone"),
        "accruals_ratio":  results["accruals"].get("accruals_ratio"),
        "buffett_passed":  results["buffett"].get("passed"),
        "buffett_total":   results["buffett"].get("total"),
        "buffett_checklist": results["buffett"].get("checklist"),
    }
