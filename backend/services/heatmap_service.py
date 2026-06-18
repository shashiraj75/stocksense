"""
Market Heatmap Service
Returns sector-wise % change for Indian and US markets.
"""
import time
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

# TTL cache: { market -> (timestamp, result) }
_heatmap_cache: dict[str, tuple[float, list]] = {}
_HEATMAP_TTL = 180  # 3 minutes — market tiles don't need faster than this

INDIA_SECTORS = {
    "Banking":    ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "FEDERALBNK", "IDFCFIRSTB"],
    "IT":         ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "PERSISTENT", "COFORGE", "LTTS", "KPITTECH", "OFSS"],
    "Energy":     ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL", "POWERGRID", "NTPC", "HINDPETRO", "ADANIGREEN", "TATAPOWER", "TORNTPOWER", "JSWENERGY"],
    "Auto":       ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "BOSCHLTD", "MOTHERSON", "APOLLOTYRE", "MRF", "TIINDIA"],
    "Pharma":     ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "TORNTPHARM", "AUROPHARMA", "ALKEM", "BIOCON", "GLENMARK", "IPCALAB", "ZYDUSLIFE"],
    "FMCG":       ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP", "MARICO", "COLPAL", "EMAMILTD", "TATACONSUM", "VBL", "PGHH"],
    "Metal":      ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL", "NMDC", "COALINDIA", "JINDALSTEL", "NATIONALUM", "WELSPUNLIV"],
    "Finance":    ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN", "LICHSGFIN", "SBICARD", "M&MFIN", "MANAPPURAM", "POONAWALLA", "SHRIRAMFIN"],
    "Realty":     ["DLF", "OBEROIRLTY", "GODREJPROP", "PRESTIGE", "LODHA", "PHOENIXLTD", "SOBHA", "BRIGADE", "SUNTECK", "MAHLIFE"],
    "Telecom":    ["BHARTIARTL", "IDEA", "TATACOMM", "HCLTECH"],
    "Consumer":   ["TITAN", "TRENT", "DMART", "NYKAA", "ZOMATO", "INDHOTEL", "JUBLFOOD", "DEVYANI", "SAPPHIRE", "WESTLIFE"],
    "Infra":      ["LT", "SIEMENS", "HAL", "BHEL", "ADANIPORTS", "IRCTC", "DELHIVERY", "CONCOR", "GMRINFRA", "IRB"],
}

US_SECTORS = {
    "Tech":        ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD", "INTC", "CRM", "ADBE", "ORCL"],
    "Finance":     ["JPM", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "C", "SCHW", "COF", "USB", "PNC"],
    "Healthcare":  ["JNJ", "UNH", "PFE", "MRK", "ABT", "ABBV", "LLY", "BMY", "AMGN", "GILD", "CVS", "CI"],
    "Energy":      ["XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "PSX", "VLO", "HAL"],
    "Consumer":    ["WMT", "TGT", "HD", "MCD", "NKE", "SBUX", "COST", "LOW", "AMZN", "BKNG"],
    "Industrials": ["BA", "CAT", "GE", "MMM", "HON", "UPS", "RTX", "LMT", "NOC", "DE"],
    "Telecom":     ["T", "VZ", "TMUS", "DISH", "LUMN"],
    "Utilities":   ["NEE", "DUK", "SO", "AEP", "EXC", "SRE", "PCG", "XEL", "WEC", "ES"],
    "Realty":      ["AMT", "PLD", "EQIX", "CCI", "SPG", "O", "WELL", "DLR", "PSA", "EXR"],
    "Materials":   ["LIN", "APD", "ECL", "NEM", "FCX", "DOW", "DD", "PPG", "ALB", "CF"],
}


def _bulk_changes(symbols: list[str], suffix: str) -> dict[str, float | None]:
    """
    Fetch % change for all symbols in one yf.download() call — far more reliable
    than per-stock fast_info requests from cloud IPs.
    """
    tickers = [s + suffix for s in symbols]
    changes: dict[str, float | None] = {s: None for s in symbols}
    try:
        df = yf.download(tickers, period="2d", interval="1d", progress=False, threads=True)
        close = df["Close"] if "Close" in df.columns else df.xs("Close", axis=1, level=0)
        close = close.dropna(how="all")
        if len(close) < 2:
            return changes
        prev_row  = close.iloc[-2]
        today_row = close.iloc[-1]
        for sym, ticker in zip(symbols, tickers):
            prev  = prev_row.get(ticker)
            today = today_row.get(ticker)
            if prev and today and float(prev) > 0:
                changes[sym] = round((float(today) - float(prev)) / float(prev) * 100, 2)
    except Exception as e:
        log.warning("bulk_changes download failed: %s", e)
    return changes


def get_heatmap(market: str) -> list[dict]:
    cached = _heatmap_cache.get(market)
    if cached and (time.time() - cached[0]) < _HEATMAP_TTL:
        return cached[1]

    sectors = INDIA_SECTORS if market == "IN" else US_SECTORS
    suffix  = ".NS" if market == "IN" else ""

    # Collect all unique symbols across sectors
    all_symbols = list({sym for stocks in sectors.values() for sym in stocks})

    # Single bulk download — one request instead of 125 individual ones
    all_changes = _bulk_changes(all_symbols, suffix)

    # Map back into sector structure
    results: dict[str, dict[str, float | None]] = {s: {} for s in sectors}
    for sector, stocks in sectors.items():
        for sym in stocks:
            results[sector][sym] = all_changes.get(sym)

    # Build response: per sector, top 10 by absolute move, sorted descending
    MAX_STOCKS = 10
    output = []
    for sector, stocks in sectors.items():
        stock_data = []
        changes = []
        for sym in stocks:
            chg = results[sector].get(sym)
            stock_data.append({"symbol": sym, "change_pct": chg})
            if chg is not None:
                changes.append(chg)
        sector_avg = round(sum(changes) / len(changes), 2) if changes else None

        # Sort by absolute change descending, keep top 10 significant movers
        stock_data.sort(key=lambda s: abs(s["change_pct"]) if s["change_pct"] is not None else 0, reverse=True)
        stock_data = stock_data[:MAX_STOCKS]
        # Then re-sort the top 10 descending by actual value (gainers first, losers last)
        stock_data.sort(key=lambda s: s["change_pct"] if s["change_pct"] is not None else -999, reverse=True)

        output.append({
            "sector": sector,
            "avg_change": sector_avg,
            "stocks": stock_data,
            "loaded": len(changes),
            "total": len(stocks),
        })

    output = sorted(output, key=lambda x: x["avg_change"] or 0, reverse=True)
    _heatmap_cache[market] = (time.time(), output)
    return output
