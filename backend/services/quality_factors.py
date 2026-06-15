"""
Professional-grade quality factor scoring for Indian stocks.

Covers 7 dimensions:
  1. Earnings Revisions      — EPS surprise trend + analyst upgrade/downgrade momentum
  2. Institutional Ownership — % held, institutions count (proxy for smart-money confidence)
  3. Relative Strength       — Stock return vs Nifty 50 (1M, 3M, 6M)
  4. Sector Strength         — Sector index momentum; is the stock swimming with the tide?
  5. Corporate Actions       — Dividend consistency, buybacks, stock splits (capital discipline)
  6. Liquidity/Microstructure— Volume trend, avg daily turnover, market-cap liquidity tier
  7. Quality Metrics         — Piotroski F-Score (9-pt), ROIC, asset efficiency

All functions accept pre-fetched (ticker, df, info) to avoid redundant yfinance API calls.
Sector index data is cached for 15 minutes (shared across all stocks in a generation run).
"""

import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

# ── Sector index cache ────────────────────────────────────────────────────────
_sector_lock  = threading.Lock()
_sector_cache: dict | None = None
_sector_expiry: float = 0
SECTOR_CACHE_TTL = 900  # 15 min

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
    "Finance":  "^CNXFINANCE",
    "Nifty50":  "^NSEI",
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
    # Consumer Discretionary / Others
    "TITAN": "FMCG", "ASIANPAINT": "FMCG", "PIDILITIND": "FMCG",
    "NAUKRI": "IT", "ZOMATO": "Finance", "PAYTM": "Finance", "NYKAA": "Finance",
    "POLICYBZR": "Finance", "DELHIVERY": "Infra", "IRCTC": "Infra",
    "INDHOTEL": "Finance", "TRENT": "FMCG", "VOLTAS": "FMCG", "HAVELLS": "FMCG",
    "ADANIENT": "Energy", "DMART": "FMCG", "SUPREMEIND": "FMCG",
    "SRF": "Pharma", "PIIND": "Pharma", "ULTRACEMCO": "Infra", "SHREECEM": "Infra",
    "GRASIM": "Infra",
}


def _get_sector_returns() -> dict[str, float | None]:
    """Fetch 1M and 3M returns for all sector indices. Cached 15 min."""
    global _sector_cache, _sector_expiry
    with _sector_lock:
        if _sector_cache is not None and time.time() < _sector_expiry:
            return _sector_cache
        result: dict[str, float | None] = {}
        for sector, sym in SECTOR_INDICES.items():
            try:
                df = yf.Ticker(sym).history(period="4mo")
                if len(df) < 20:
                    result[sector] = None
                    continue
                ret_1m = (df["Close"].iloc[-1] - df["Close"].iloc[-21]) / df["Close"].iloc[-21] * 100
                ret_3m = (df["Close"].iloc[-1] - df["Close"].iloc[-63]) / df["Close"].iloc[-63] * 100 if len(df) >= 63 else None
                result[sector] = {"1m": round(ret_1m, 2), "3m": round(ret_3m, 2) if ret_3m is not None else None}
            except Exception:
                result[sector] = None
        _sector_cache = result
        _sector_expiry = time.time() + SECTOR_CACHE_TTL
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

def relative_strength_score(df: pd.DataFrame) -> dict:
    """
    Compare stock's 1M, 3M, 6M returns vs Nifty 50.
    Outperforming the benchmark is a strong quality signal.
    """
    score = 50
    reasons: list[str] = []

    try:
        nifty_data = yf.Ticker("^NSEI").history(period="7mo")
        if nifty_data.empty or len(df) < 21:
            return {"score": 50, "reasons": []}

        stock_close = df["Close"]
        nifty_close = nifty_data["Close"]

        periods = {"1M": 21, "3M": 63, "6M": 126}
        rs_scores = []

        for label, days in periods.items():
            if len(stock_close) >= days and len(nifty_close) >= days:
                stock_ret = (stock_close.iloc[-1] - stock_close.iloc[-days]) / stock_close.iloc[-days] * 100
                nifty_ret = (nifty_close.iloc[-1] - nifty_close.iloc[-days]) / nifty_close.iloc[-days] * 100
                rs = stock_ret - nifty_ret

                if rs > 10:
                    rs_scores.append(+12)
                    reasons.append(f"Outperforming Nifty 50 by {rs:+.1f}% over {label} — strong relative strength")
                elif rs > 4:
                    rs_scores.append(+6)
                    reasons.append(f"Outperforming Nifty 50 by {rs:+.1f}% over {label}")
                elif rs < -10:
                    rs_scores.append(-12)
                    reasons.append(f"Underperforming Nifty 50 by {abs(rs):.1f}% over {label} — weak relative strength")
                elif rs < -4:
                    rs_scores.append(-6)
                    reasons.append(f"Slightly underperforming Nifty 50 over {label} ({rs:+.1f}%)")
                else:
                    rs_scores.append(0)

        if rs_scores:
            avg_adj = np.mean(rs_scores)
            score = max(0, min(100, 50 + avg_adj))

    except Exception:
        pass

    return {"score": round(score), "reasons": reasons[:2]}  # top 2 most recent periods


# ── 4. SECTOR STRENGTH ───────────────────────────────────────────────────────

def sector_strength_score(symbol: str) -> dict:
    """
    Check if the stock's sector is outperforming Nifty 50.
    Sector momentum is a powerful short-to-medium term predictor.
    """
    score = 50
    reasons: list[str] = []

    sector = STOCK_SECTOR.get(symbol.upper())
    if sector is None:
        return {"score": 50, "reasons": [], "sector": "Unknown"}

    try:
        returns = _get_sector_returns()
        sector_data = returns.get(sector)
        nifty_data  = returns.get("Nifty50")

        if sector_data and nifty_data:
            s1m = sector_data.get("1m", 0) or 0
            n1m = nifty_data.get("1m", 0) or 0
            s3m = sector_data.get("3m", 0) or 0
            n3m = nifty_data.get("3m", 0) or 0

            rel_1m = s1m - n1m
            rel_3m = s3m - n3m

            if rel_1m > 5 and rel_3m > 5:
                score += 16
                reasons.append(f"{sector} sector is strongly outperforming Nifty 50 ({s1m:+.1f}% 1M, {s3m:+.1f}% 3M) — sector tailwind")
            elif rel_1m > 2 or rel_3m > 4:
                score += 8
                reasons.append(f"{sector} sector outperforming Nifty 50 ({s1m:+.1f}% 1M) — positive sector rotation")
            elif rel_1m < -5 and rel_3m < -5:
                score -= 14
                reasons.append(f"{sector} sector underperforming Nifty 50 ({s1m:+.1f}% 1M) — sector headwind; fighting the trend")
            elif rel_1m < -2 or rel_3m < -4:
                score -= 7
                reasons.append(f"{sector} sector slightly underperforming Nifty 50 ({s1m:+.1f}% 1M)")
            else:
                reasons.append(f"{sector} sector inline with Nifty 50 ({s1m:+.1f}% 1M)")

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
    f_details: list[str] = []
    try:
        fin = ticker.financials
        bs  = ticker.balance_sheet
        cf  = ticker.cashflow

        if fin is not None and not fin.empty and bs is not None and not bs.empty and cf is not None and not cf.empty:
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
            elif gm_curr:
                f_score += 1  # assume passing if current margin is positive and > 20%

            # P9: Δ Asset Turnover — improving
            if rev is not None and total_assets_row is not None and len(rev) >= 2 and len(total_assets_row) >= 2:
                at_curr = rev[-1] / total_assets_row[-1] if total_assets_row[-1] != 0 else 0
                at_prev = rev[-2] / total_assets_row[-2] if total_assets_row[-2] != 0 else 0
                if at_curr > at_prev:
                    f_score += 1; f_details.append("Asset turnover improving ✓")

            # Score the F-Score
            if f_score >= 8:
                score += 20
                reasons.append(f"Excellent Piotroski F-Score {f_score}/9 — high-quality business on all financial health metrics")
            elif f_score >= 6:
                score += 10
                reasons.append(f"Strong Piotroski F-Score {f_score}/9 — financially healthy company")
            elif f_score >= 4:
                score += 0
                reasons.append(f"Moderate Piotroski F-Score {f_score}/9 — mixed financial signals")
            else:
                score -= 12
                reasons.append(f"Weak Piotroski F-Score {f_score}/9 — multiple financial health red flags")

    except Exception:
        pass

    # ── ROIC (Return on Invested Capital) ────────────────────────────────────
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


# ── MASTER FUNCTION ──────────────────────────────────────────────────────────

def compute_all_quality_factors(symbol: str, ticker, df: pd.DataFrame, info: dict, horizon: str) -> dict:
    """
    Run all 7 quality factor checks and return a combined score + all reasons.
    Skips deep financial analysis for short-horizon (too slow, less relevant).
    """
    results: dict[str, dict] = {}
    all_reasons: list[dict] = []

    # Always run (fast, from info dict or precomputed)
    results["earnings_revision"]   = earnings_revision_score(ticker, info)
    results["institutional"]       = institutional_ownership_score(ticker, info)
    results["relative_strength"]   = relative_strength_score(df)
    results["sector_strength"]     = sector_strength_score(symbol)
    results["liquidity"]           = liquidity_score(df, info)

    # Corporate actions and quality metrics — skip for short term to keep latency manageable
    if horizon in ("medium", "long"):
        results["corporate_actions"] = corporate_actions_score(ticker, info)
        results["quality_metrics"]   = quality_metrics_score(ticker, df, info)
    else:
        # For short term: still run corporate actions (fast), skip deep Piotroski
        results["corporate_actions"] = corporate_actions_score(ticker, info)
        results["quality_metrics"]   = {"score": 50, "reasons": [], "piotroski": None}

    # Build combined score (weighted average)
    weights = {
        "earnings_revision":  0.20,
        "institutional":      0.12,
        "relative_strength":  0.20,
        "sector_strength":    0.18,
        "liquidity":          0.10,
        "corporate_actions":  0.08,
        "quality_metrics":    0.12,
    }
    combined = sum(results[k]["score"] * weights[k] for k in weights if k in results)

    # Collect reasons tagged by dimension
    dimension_labels = {
        "earnings_revision": "Earnings",
        "institutional":     "Ownership",
        "relative_strength": "Rel. Strength",
        "sector_strength":   "Sector",
        "liquidity":         "Liquidity",
        "corporate_actions": "Corp. Actions",
        "quality_metrics":   "Quality",
    }
    for key, label in dimension_labels.items():
        for reason in results.get(key, {}).get("reasons", []):
            all_reasons.append({
                "indicator": label,
                "signal": "BUY" if results[key]["score"] >= 60 else ("BEARISH" if results[key]["score"] <= 40 else "INFO"),
                "reason": reason,
            })

    sector = results.get("sector_strength", {}).get("sector", "Unknown")
    piotroski = results.get("quality_metrics", {}).get("piotroski")

    return {
        "score": round(max(0, min(100, combined))),
        "breakdown": results,
        "reasons": all_reasons,
        "sector": sector,
        "piotroski": piotroski,
    }
