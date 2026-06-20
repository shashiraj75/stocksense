"""
Market Heatmap Service
Returns sector-wise % change for Indian and US markets.

India: Uses NSE official sector indices — one API call per sector, live data,
no rate limits, no crumb issues. Falls back to yfinance bulk download.
US: Uses yfinance bulk download (still reliable from Render for US equities).
"""
import time
import logging
import pandas as pd
import yfinance as yf
from services import nse_client

log = logging.getLogger(__name__)

# TTL cache: { market -> (timestamp, result) }
_heatmap_cache: dict[str, tuple[float, list]] = {}
_HEATMAP_TTL = 300  # 5 minutes — market tiles don't need faster than this

INDIA_SECTORS = {
    "Banking":      ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "FEDERALBNK", "IDFCFIRSTB", "INDIANB", "BANKINDIA", "AUBANK", "RBLBANK", "DCBBANK", "KARURVYSYA"],
    "IT":           ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "TATAELXSI", "MPHASIS", "PERSISTENT", "COFORGE", "LTTS", "KPITTECH", "OFSS", "DIXON", "CYIENT", "BIRLASOFT", "MASTEK", "NIITTECH", "ZENSAR"],
    "Energy":       ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL", "POWERGRID", "NTPC", "HINDPETRO", "ADANIGREEN", "TATAPOWER", "TORNTPOWER", "JSWENERGY", "CESC", "ADANITRANS", "IGL", "MGL", "PETRONET"],
    "Auto":         ["MARUTI", "TATATECH", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "BOSCHLTD", "MOTHERSON", "APOLLOTYRE", "MRF", "TIINDIA", "CROMPTON", "TATAMOTORS", "ASHOKLEY", "ESCORTS", "FORCEMOT", "BALKRISIND"],
    "Pharma":       ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "TORNTPHARM", "AUROPHARMA", "ALKEM", "BIOCON", "GLENMARK", "IPCALAB", "ZYDUSLIFE", "ABBOTINDIA", "PFIZER", "SANOFI", "NATCOPHARM", "GRANULES"],
    "Healthcare":   ["APOLLOHOSP", "FORTIS", "MAXHEALTH", "NARAYANA", "METROPOLIS", "LALPATHLAB", "THYROCARE", "KRSNAA", "MEDANTA", "ASTER"],
    "FMCG":         ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP", "MARICO", "COLPAL", "EMAMILTD", "TATACONSUM", "VBL", "PGHH", "BIKAJI", "PATANJALI", "JYOTHYLAB", "VENKEYS"],
    "Finance":      ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN", "LICHSGFIN", "SBICARD", "ABCAPITAL", "MANAPPURAM", "POONAWALLA", "SHRIRAMFIN", "M&MFIN", "SUNDARMFIN", "AAVAS", "HOMEFIRST", "CREDITACC"],
    "Insurance":    ["HDFCLIFE", "SBILIFE", "ICICIPRULIFE", "MAXFINSERV", "LICI", "GICRE", "NIACL", "STARHEALTH"],
    "Metal":        ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL", "NMDC", "COALINDIA", "JINDALSTEL", "NATIONALUM", "POLYCAB", "HINDZINC", "APLAPOLLO", "RATNAMANI"],
    "Chemicals":    ["PIDILITIND", "SRF", "DEEPAKNTR", "NAVINFLUOR", "AARTIIND", "ALKYLAMINE", "BALAJI-AMINES", "CLEAN", "FINEORG", "GALAXYSURF", "TATACHEM", "GHCL"],
    "Cement":       ["ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "JKCEMENT", "RAMCOCEM", "HEIDELBERG", "BIRLACORPN", "INDIACEM", "DALMIACEM"],
    "Defence":      ["HAL", "BEL", "MIDHANI", "MAZDOCK", "COCHINSHIP", "BEML", "BHEL", "PARAS", "DATAPATTNS", "GRSE"],
    "Realty":       ["DLF", "OBEROIRLTY", "GODREJPROP", "PRESTIGE", "LODHA", "PHOENIXLTD", "SOBHA", "BRIGADE", "SUNTECK", "MAHLIFE", "KOLTEPATIL", "SIGNATURE", "RAYMOND"],
    "Telecom":      ["BHARTIARTL", "IDEA", "TATACOMM", "INDUSTOWER", "HFCL", "STLTECH"],
    "Consumer":     ["TITAN", "TRENT", "DMART", "NYKAA", "ETERNAL", "INDHOTEL", "JUBLFOOD", "DEVYANI", "SAPPHIRE", "WESTLIFE", "KALYANKJIL", "SENCO", "PCJEWELLER", "MANYAVAR"],
    "Infra":        ["LT", "SIEMENS", "ADANIPORTS", "IRCTC", "DELHIVERY", "CONCOR", "GMRAIRPORT", "IRB", "KNR", "ASHOKA", "PNCINFRA", "RITES", "IRCON", "AHLUCONT"],
    "Capital Goods":["ABB", "CUMMINSIND", "THERMAX", "BHEL", "GRINDWELL", "SCHAEFFLER", "TIMKEN", "SKFINDIA", "ELGIEQUIP", "VOLTAMP"],
}

US_SECTORS = {
    "Tech":          ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA", "AMD", "INTC", "CRM", "ADBE", "ORCL", "NOW", "SNOW", "PLTR", "UBER", "LYFT", "ABNB"],
    "Semiconductors":["NVDA", "TSM", "AVGO", "QCOM", "MU", "ASML", "AMAT", "LRCX", "KLAC", "MRVL", "MCHP", "SWKS", "ON", "TXN", "ADI"],
    "Finance":       ["JPM", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "C", "SCHW", "COF", "USB", "PNC", "TFC", "FITB", "KEY", "ALLY", "SYF"],
    "Healthcare":    ["JNJ", "UNH", "PFE", "MRK", "ABT", "ABBV", "LLY", "BMY", "CVS", "CI", "HCA", "THC", "ELV", "CNC", "MOH"],
    "Biotech":       ["AMGN", "GILD", "BIIB", "REGN", "VRTX", "MRNA", "BNTX", "SGEN", "ALNY", "INCY", "EXAS", "ILMN", "BEAM", "CRSP"],
    "Energy":        ["XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "PSX", "VLO", "HAL", "BKR", "DVN", "FANG", "MRO"],
    "Consumer Disc": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TGT", "EBAY", "ETSY", "ROST", "TJX", "DG"],
    "Consumer Stap": ["WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "CL", "GIS", "K", "HSY", "CPB", "SJM"],
    "Industrials":   ["BA", "CAT", "GE", "HON", "UPS", "RTX", "LMT", "NOC", "DE", "MMM", "FDX", "CSX", "NSC", "UNP", "EMR"],
    "Telecom":       ["T", "VZ", "TMUS", "CHTR", "CMCSA", "DIS", "NFLX", "WBD"],
    "Utilities":     ["NEE", "DUK", "SO", "AEP", "EXC", "SRE", "PCG", "XEL", "WEC", "ES", "D", "ETR"],
    "Realty":        ["AMT", "PLD", "EQIX", "CCI", "SPG", "O", "WELL", "DLR", "PSA", "EXR", "VICI", "ARE", "SBA"],
    "Materials":     ["LIN", "APD", "ECL", "NEM", "FCX", "DOW", "DD", "PPG", "ALB", "CF", "MOS", "IP", "PKG"],
}


def _bulk_changes(symbols: list[str], suffix: str) -> dict[str, float | None]:
    """
    Fetch % change for all symbols in one yf.download() call — far more reliable
    than per-stock fast_info requests from cloud IPs.
    """
    tickers = [s + suffix for s in symbols]
    changes: dict[str, float | None] = {s: None for s in symbols}
    try:
        # period="5d" ensures we always have ≥2 rows of settled trading data.
        # period="2d" breaks when today's row is all-NaN (market just closed,
        # data not yet published) — dropna leaves only 1 row → len < 2 → all None.
        df = yf.download(tickers, period="5d", interval="1d", progress=False)
        if df.empty:
            return changes
        if isinstance(df.columns, pd.MultiIndex):
            # MultiIndex: ("Close", ticker)
            close = df["Close"] if "Close" in df.columns.get_level_values(0) else None
        else:
            close = df[["Close"]] if "Close" in df.columns else None
        if close is None:
            return changes
        close = close.dropna(how="all")
        if len(close) < 2:
            return changes
        prev_row  = close.iloc[-2]
        today_row = close.iloc[-1]
        for sym, ticker in zip(symbols, tickers):
            prev  = prev_row.get(ticker)
            today = today_row.get(ticker)
            if prev is not None and today is not None and float(prev) > 0:
                changes[sym] = round((float(today) - float(prev)) / float(prev) * 100, 2)
    except Exception as e:
        err = str(e).lower()
        if "crumb" in err or "401" in err or "unauthorized" in err:
            try:
                yf.utils.get_crumb(force=True) if hasattr(yf.utils, "get_crumb") else None
                log.info("bulk_changes: crumb refreshed after 401")
            except Exception:
                pass
        log.warning("bulk_changes download failed: %s", e)
    return changes


def _nse_sector_changes() -> dict[str, dict[str, float | None]]:
    """
    Fetch per-stock change% for India using NSE sector index APIs.
    Returns {sector: {symbol: change_pct}}.
    Each sector index is one HTTP call — far faster than bulk yfinance download.
    """
    results: dict[str, dict[str, float | None]] = {}
    for sector in INDIA_SECTORS:
        nse_changes = nse_client.get_sector_changes(sector)
        if nse_changes:
            results[sector] = nse_changes
        else:
            results[sector] = {}
    return results


def _build_sector_output(sectors: dict, all_changes: dict[str, dict[str, float | None]]) -> list[dict]:
    MAX_STOCKS = 15
    output = []
    for sector, stocks in sectors.items():
        sector_changes = all_changes.get(sector, {})
        stock_data = []
        changes = []
        for sym in stocks:
            chg = sector_changes.get(sym)
            stock_data.append({"symbol": sym, "change_pct": chg})
            if chg is not None:
                changes.append(chg)
        sector_avg = round(sum(changes) / len(changes), 2) if changes else None
        stock_data.sort(key=lambda s: abs(s["change_pct"]) if s["change_pct"] is not None else 0, reverse=True)
        stock_data = stock_data[:MAX_STOCKS]
        stock_data.sort(key=lambda s: s["change_pct"] if s["change_pct"] is not None else -999, reverse=True)
        output.append({
            "sector":     sector,
            "avg_change": sector_avg,
            "stocks":     stock_data,
            "loaded":     len(changes),
            "total":      len(stocks),
        })
    return sorted(output, key=lambda x: x["avg_change"] or 0, reverse=True)


_last_good_heatmap: dict[str, list] = {}


def _cache_and_persist(market: str, output: list) -> list:
    _heatmap_cache[market] = (time.time(), output)
    _last_good_heatmap[market] = output
    try:
        if __import__("os").getenv("USE_POSTGRES") == "1":
            from services.postgres_store import save_market_cache
            save_market_cache(f"heatmap_{market}", output)
    except Exception:
        pass
    return output


def _load_last_good(market: str) -> list | None:
    last_good = _last_good_heatmap.get(market)
    if last_good:
        return last_good
    try:
        if __import__("os").getenv("USE_POSTGRES") == "1":
            from services.postgres_store import load_market_cache
            last_good = load_market_cache(f"heatmap_{market}")
            if last_good:
                _last_good_heatmap[market] = last_good
                return last_good
    except Exception:
        pass
    return None


def get_heatmap(market: str) -> list[dict]:
    cached = _heatmap_cache.get(market)
    if cached and (time.time() - cached[0]) < _HEATMAP_TTL:
        return cached[1]

    if market == "IN":
        # PRIMARY: NSE sector index APIs — live, reliable, no rate limits
        try:
            nse_results = _nse_sector_changes()
            has_data = any(bool(v) for v in nse_results.values())
            if has_data:
                output = _build_sector_output(INDIA_SECTORS, nse_results)
                log.info("heatmap IN: NSE sector APIs returned data for %d sectors",
                         sum(1 for v in nse_results.values() if v))
                return _cache_and_persist(market, output)
        except Exception as e:
            log.warning("NSE heatmap failed, falling back to yfinance: %s", e)

        # FALLBACK: yfinance bulk download
        all_symbols = list({sym for stocks in INDIA_SECTORS.values() for sym in stocks})
        flat_changes = _bulk_changes(all_symbols, ".NS")
        all_changes = {sector: {sym: flat_changes.get(sym) for sym in stocks}
                       for sector, stocks in INDIA_SECTORS.items()}
        output = _build_sector_output(INDIA_SECTORS, all_changes)

    else:  # US
        all_symbols = list({sym for stocks in US_SECTORS.values() for sym in stocks})
        flat_changes = _bulk_changes(all_symbols, "")
        all_changes = {sector: {sym: flat_changes.get(sym) for sym in stocks}
                       for sector, stocks in US_SECTORS.items()}
        output = _build_sector_output(US_SECTORS, all_changes)

    has_data = any(s["loaded"] > 0 for s in output)
    if has_data:
        return _cache_and_persist(market, output)

    # No fresh data — fall back to last known good (memory → Postgres → empty)
    return _load_last_good(market) or output
