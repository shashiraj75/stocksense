"""
Market Heatmap Service
Returns sector-wise % change for Indian and US markets.
"""
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    try:
        t = yf.Ticker(symbol + suffix)
        info = t.fast_info
        price = float(info.last_price) if info.last_price else None
        prev  = float(info.previous_close) if info.previous_close else None
        if price and prev and prev > 0:
            return symbol, round((price - prev) / prev * 100, 2)
    except Exception:
        pass
    return symbol, None


def get_heatmap(market: str) -> list[dict]:
    sectors = INDIA_SECTORS if market == "IN" else US_SECTORS
    suffix  = ".NS" if market == "IN" else ""

    tasks: list[tuple[str, str, str]] = []
    for sector, stocks in sectors.items():
        for sym in stocks:
            tasks.append((sector, sym, suffix))

    # Fetch all in parallel
    results: dict[str, dict[str, float | None]] = {s: {} for s in sectors}
    with ThreadPoolExecutor(max_workers=10) as pool:
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
        })

    return sorted(output, key=lambda x: x["avg_change"] or 0, reverse=True)
