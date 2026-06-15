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
    "Banking":    ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB"],
    "IT":         ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "PERSISTENT"],
    "Energy":     ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL", "POWERGRID", "NTPC", "HINDPETRO"],
    "Auto":       ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR"],
    "Pharma":     ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "TORNTPHARM", "AUROPHARMA"],
    "FMCG":       ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP", "MARICO"],
    "Metal":      ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL", "NMDC", "COALINDIA"],
    "Realty":     ["DLF", "OBEROIRLTY", "GODREJPROP", "PRESTIGE"],
    "Telecom":    ["BHARTIARTL", "IDEA"],
    "Finance":    ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN", "LICHSGFIN"],
}

US_SECTORS = {
    "Tech":       ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA"],
    "Finance":    ["JPM", "BAC", "GS", "MS", "WFC", "AXP", "BLK"],
    "Healthcare": ["JNJ", "UNH", "PFE", "MRK", "ABT", "ABBV", "LLY"],
    "Energy":     ["XOM", "CVX", "COP", "SLB", "OXY", "EOG"],
    "Consumer":   ["WMT", "TGT", "HD", "MCD", "NKE", "SBUX", "COST"],
    "Industrials":["BA", "CAT", "GE", "MMM", "HON", "UPS", "RTX"],
    "Telecom":    ["T", "VZ", "TMUS"],
    "Utilities":  ["NEE", "DUK", "SO", "AEP"],
    "Realty":     ["AMT", "PLD", "EQIX", "CCI"],
    "Materials":  ["LIN", "APD", "ECL", "NEM"],
}


def _fetch_change(symbol: str, suffix: str = "") -> tuple[str, float | None]:
    full = symbol + suffix
    for attempt in range(3):
        try:
            t = yf.Ticker(full)
            fi = t.fast_info
            price = float(fi.last_price) if fi.last_price else None
            prev  = float(fi.previous_close) if fi.previous_close else None
            if price and prev and prev > 0:
                return symbol, round((price - prev) / prev * 100, 2)
            break  # got a response, just no data — don't retry
        except Exception as e:
            if "rate" in str(e).lower() and attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                break
    # Fallback: history last 2 days
    try:
        df = yf.Ticker(full).history(period="2d")
        if len(df) >= 2:
            close = df["Close"].dropna()
            if len(close) >= 2:
                return symbol, round((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100, 2)
    except Exception:
        pass
    return symbol, None


def get_heatmap(market: str) -> list[dict]:
    cached = _heatmap_cache.get(market)
    if cached and (time.time() - cached[0]) < _HEATMAP_TTL:
        return cached[1]

    sectors = INDIA_SECTORS if market == "IN" else US_SECTORS
    suffix  = ".NS" if market == "IN" else ""

    tasks: list[tuple[str, str, str]] = []
    for sector, stocks in sectors.items():
        for sym in stocks:
            tasks.append((sector, sym, suffix))

    # Fetch all in parallel — bump workers to saturate yfinance faster
    results: dict[str, dict[str, float | None]] = {s: {} for s in sectors}
    with ThreadPoolExecutor(max_workers=25) as pool:
        futures = {pool.submit(_fetch_change, sym, sfx): (sec, sym) for sec, sym, sfx in tasks}
        for future in as_completed(futures):
            sec, sym = futures[future]
            _, change = future.result()
            results[sec][sym] = change

    # Build response: per sector, list of stocks with change_pct
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
