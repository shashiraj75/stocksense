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
    # ── Large / well-known ───────────────────────────────────────────────────
    "Banking":        ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "UNIONBANK", "FEDERALBNK", "IDFCFIRSTB", "INDIANB", "BANKINDIA", "AUBANK", "RBLBANK", "DCBBANK", "KARURVYSYA", "YESBANK", "LAKSHVILAS"],
    "IT":             ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "TATAELXSI", "MPHASIS", "PERSISTENT", "COFORGE", "LTTS", "KPITTECH", "OFSS", "DIXON", "CYIENT", "BIRLASOFT", "MASTEK", "ZENSAR", "HEXAWARE", "RATEGAIN", "INTELLECT"],
    "Auto":           ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "BOSCHLTD", "MOTHERSON", "APOLLOTYRE", "MRF", "TIINDIA", "BALKRISIND", "ASHOKLEY", "ESCORTS", "FORCEMOT", "TATATECH", "SUNDRMFAST", "EXIDEIND", "AMARAJABAT"],
    "Pharma":         ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "TORNTPHARM", "AUROPHARMA", "ALKEM", "BIOCON", "GLENMARK", "IPCALAB", "ZYDUSLIFE", "ABBOTINDIA", "PFIZER", "SANOFI", "NATCOPHARM", "GRANULES", "LAURUSLABS", "SOLARA", "SEQUENT"],
    "Energy":         ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL", "POWERGRID", "NTPC", "HINDPETRO", "ADANIGREEN", "TATAPOWER", "TORNTPOWER", "JSWENERGY", "CESC", "IGL", "MGL", "PETRONET", "GUJARATGAS", "ADANIGAS", "MAHAGAS", "GUJRATGAS"],
    "FMCG":           ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA", "DABUR", "GODREJCP", "MARICO", "COLPAL", "EMAMILTD", "TATACONSUM", "VBL", "PGHH", "BIKAJI", "JYOTHYLAB", "VENKEYS", "VSTIND", "RADICO", "UNITDSPR"],
    "Finance":        ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "MUTHOOTFIN", "LICHSGFIN", "SBICARD", "ABCAPITAL", "MANAPPURAM", "POONAWALLA", "SHRIRAMFIN", "M&MFIN", "SUNDARMFIN", "AAVAS", "HOMEFIRST", "CREDITACC", "UGROCAP", "CAPSFIN", "MOTILALOFS"],
    # ── New sectors ─────────────────────────────────────────────────────────
    "Healthcare":     ["APOLLOHOSP", "FORTIS", "MAXHEALTH", "NARAYANA", "METROPOLIS", "LALPATHLAB", "THYROCARE", "KRSNAA", "MEDANTA", "ASTER", "RAINBOW", "YATHARTH", "SAGILITY", "VIJAYAHOSP"],
    "Insurance":      ["HDFCLIFE", "SBILIFE", "ICICIPRULIFE", "MAXFINSERV", "LICI", "GICRE", "NIACL", "STARHEALTH", "POLICYBZR"],
    "Chemicals":      ["PIDILITIND", "SRF", "DEEPAKNTR", "NAVINFLUOR", "AARTIIND", "ALKYLAMINE", "CLEAN", "FINEORG", "GALAXYSURF", "TATACHEM", "GHCL", "VINATI", "NOCIL", "SUDARSCHEM", "ATUL", "ROSSARI"],
    "Cement":         ["ULTRACEMCO", "SHREECEM", "AMBUJACEM", "ACC", "JKCEMENT", "RAMCOCEM", "HEIDELBERG", "BIRLACORPN", "INDIACEM", "DALMIACEM", "JKLAKSHMI", "NUVOCO", "ORIENT"],
    "Metal & Mining": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "SAIL", "NMDC", "COALINDIA", "JINDALSTEL", "NATIONALUM", "POLYCAB", "HINDZINC", "APLAPOLLO", "RATNAMANI", "JINDALSAW", "WELSPUNLIV", "SHYAMMETL"],
    "Defence":        ["HAL", "BEL", "MIDHANI", "MAZDOCK", "COCHINSHIP", "BEML", "PARAS", "DATAPATTNS", "GRSE", "MTAR", "IDEAFORGE", "ZENTECH", "SOLARINDS", "BHEL"],
    "Realty":         ["DLF", "OBEROIRLTY", "GODREJPROP", "PRESTIGE", "LODHA", "PHOENIXLTD", "SOBHA", "BRIGADE", "SUNTECK", "MAHLIFE", "KOLTEPATIL", "SIGNATURE", "RAYMOND", "ANANTRAJ", "KEYSTONE"],
    "Telecom":        ["BHARTIARTL", "IDEA", "TATACOMM", "INDUSTOWER", "HFCL", "STLTECH", "TEJAS", "BSOFT"],
    "Consumer Disc":  ["TITAN", "TRENT", "DMART", "NYKAA", "ETERNAL", "KALYANKJIL", "SENCO", "PCJEWELLER", "MANYAVAR", "VMART", "SHOPERSTOP", "CAMPUS", "BATAINDIA", "METROBRAND", "RELAXO"],
    "Hotels & Travel":["INDHOTEL", "EIHOTEL", "LEMONTRE", "CHALET", "MAHINDRA", "IRCTC", "THOMASCOOK", "MHRIL", "SPICEJET", "INDIGO"],
    "Food & Beverage":["JUBLFOOD", "DEVYANI", "SAPPHIRE", "WESTLIFE", "BARBEQUE", "ZOMATO", "SWIGGY", "TASTYBITE", "KRBL", "LT", "PATANJALI"],
    "Media & Entmt":  ["ZEEL", "SUNTV", "PVRINOX", "TIPS", "NAZARA", "NETWEB", "SAREGAMA", "BALAJITELE", "TVTODAY"],
    "Textiles":       ["PAGEIND", "WELSPUNIND", "TRIDENT", "VARDHMAN", "KPRMILL", "RUPA", "GOKEX", "ARVIND", "RAYMOND", "ALOKTEXT"],
    "Agro & Chemicals":["UPL", "PIIND", "DHANUKA", "BAYER", "RALLIS", "SUMICHEM", "INSECTICID", "DHAMPURSUG", "BALRAMCHIN", "TRIVENI"],
    "Logistics":      ["DELHIVERY", "BLUEDART", "TCIEXPRESS", "MAHLOG", "GATI", "CONCOR", "ADANIPORTS", "VRL", "ALLCARGO", "DTDC"],
    "Paints":         ["ASIANPAINT", "BERGEPAINT", "KANSAINER", "AKZOINDIA", "INDIGOPNTS", "SHALPAINTS"],
    "Infra":          ["LT", "SIEMENS", "ADANIPORTS", "GMRAIRPORT", "IRB", "KNR", "ASHOKA", "PNCINFRA", "RITES", "IRCON", "AHLUCONT", "NCC", "HG INFRA", "HGINFRA", "PTC", "ENGINERSIN"],
    "Capital Goods":  ["ABB", "CUMMINSIND", "THERMAX", "GRINDWELL", "SCHAEFFLER", "TIMKEN", "SKFINDIA", "ELGIEQUIP", "VOLTAMP", "BHEL", "ISGEC", "KIRLOSENG", "GREENPANEL"],
    "Power":          ["POWERGRID", "NTPC", "TATAPOWER", "ADANIGREEN", "JSWENERGY", "TORNTPOWER", "CESC", "NHPC", "SJVN", "RECLTD", "PFC", "IREDA", "GREENKO"],
    "EV & New Energy":["TATAPOWER", "ADANIGREEN", "GREENKO", "WAAREE", "PREMIER", "OLECTRA", "GOLDSTAR", "IEX", "MAPMYINDIA"],
}

US_SECTORS = {
    # ── Core sectors ────────────────────────────────────────────────────────
    "Mega Cap Tech":  ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "ORCL", "ADBE", "CRM"],
    "Semiconductors": ["NVDA", "TSM", "AVGO", "QCOM", "MU", "ASML", "AMAT", "LRCX", "KLAC", "MRVL", "MCHP", "ON", "TXN", "ADI", "SWKS", "MPWR", "NXPI"],
    "Cloud & SaaS":   ["MSFT", "AMZN", "GOOGL", "CRM", "NOW", "SNOW", "DDOG", "MDB", "NET", "ZS", "HUBS", "WDAY", "TEAM", "BILL", "OKTA"],
    "Cybersecurity":  ["CRWD", "PANW", "FTNT", "ZS", "OKTA", "S", "CYBR", "QLYS", "TENB", "VRNS", "RPD", "SAIL"],
    "Fintech":        ["V", "MA", "PYPL", "SQ", "AFRM", "SOFI", "COIN", "HOOD", "UPST", "LC", "PAYO", "WEX"],
    "Finance":        ["JPM", "BAC", "GS", "MS", "WFC", "AXP", "BLK", "C", "SCHW", "COF", "USB", "PNC", "TFC", "FITB", "KEY", "ALLY", "SYF", "BX", "KKR", "APO"],
    "Insurance":      ["BRK-B", "MET", "PRU", "AFL", "ALL", "TRV", "CB", "AIG", "HIG", "PGR", "UNM", "CINF"],
    "Healthcare":     ["JNJ", "UNH", "PFE", "MRK", "ABT", "ABBV", "LLY", "BMY", "CVS", "CI", "HCA", "ELV", "CNC", "MOH", "THC"],
    "Biotech":        ["AMGN", "GILD", "BIIB", "REGN", "VRTX", "MRNA", "BNTX", "ALNY", "INCY", "EXAS", "ILMN", "BEAM", "CRSP", "NTLA", "EDIT"],
    "Med Devices":    ["MDT", "ABT", "SYK", "BSX", "EW", "ZBH", "BDX", "RMD", "ISRG", "HOLX", "TFX", "ALGN"],
    "Energy":         ["XOM", "CVX", "COP", "SLB", "OXY", "EOG", "MPC", "PSX", "VLO", "HAL", "BKR", "DVN", "MRO", "APA", "FANG"],
    "Clean Energy":   ["NEE", "ENPH", "SEDG", "RUN", "FSLR", "PLUG", "BE", "CWEN", "AES", "BEP", "NOVA", "ARRY"],
    "EV":             ["TSLA", "RIVN", "LCID", "NIO", "XPEV", "LI", "FSR", "NKLA", "GOEV", "WKHS", "BLNK", "CHPT"],
    "Consumer Disc":  ["AMZN", "HD", "MCD", "NKE", "SBUX", "LOW", "BKNG", "TGT", "ROST", "TJX", "DG", "DLTR", "BBY", "W"],
    "Consumer Stap":  ["WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "CL", "GIS", "K", "HSY", "CPB", "SJM", "KHC"],
    "E-commerce":     ["AMZN", "SHOP", "EBAY", "ETSY", "W", "CHWY", "CPNG", "SE", "PDD", "JD", "BABA"],
    "Social Media":   ["META", "SNAP", "PINS", "RDDT", "MTCH", "BMBL", "YELP", "SPOT"],
    "Streaming & Media":["NFLX", "DIS", "WBD", "PARA", "FOX", "AMZN", "AAPL", "ROKU", "FUBO", "LGF-A"],
    "Gaming":         ["MSFT", "ATVI", "EA", "TTWO", "RBLX", "U", "DKNG", "PENN", "RSI", "MGAM"],
    "Aerospace & Defence":["LMT", "RTX", "NOC", "BA", "GD", "HII", "TDG", "HEI", "WWD", "AXON", "KTOS", "PLTR"],
    "Industrials":    ["CAT", "GE", "HON", "UPS", "DE", "MMM", "FDX", "CSX", "NSC", "UNP", "EMR", "ROK", "PH", "ETN", "AME"],
    "Airlines":       ["DAL", "UAL", "AAL", "LUV", "ALK", "JBLU", "SAVE", "ULCC", "HA"],
    "Cruise & Hotels":["MAR", "HLT", "H", "CCL", "RCL", "NCLH", "WYNN", "MGM", "LVS", "CZR", "VICI"],
    "Restaurants":    ["MCD", "SBUX", "CMG", "YUM", "DPZ", "WEN", "QSR", "JACK", "SHAK", "TXRH", "DENN"],
    "Retail":         ["WMT", "COST", "HD", "TGT", "LOW", "TJX", "ROST", "DG", "DLTR", "BBY", "M", "KSS"],
    "Telecom":        ["T", "VZ", "TMUS", "CHTR", "CMCSA", "DISH", "LUMN"],
    "Utilities":      ["NEE", "DUK", "SO", "AEP", "EXC", "SRE", "PCG", "XEL", "WEC", "ES", "D", "ETR", "AWK"],
    "Realty":         ["AMT", "PLD", "EQIX", "CCI", "SPG", "O", "WELL", "DLR", "PSA", "EXR", "VICI", "ARE", "SBA", "IRM"],
    "Materials":      ["LIN", "APD", "ECL", "NEM", "FCX", "DOW", "DD", "PPG", "ALB", "CF", "MOS", "IP", "PKG", "BLL"],
    "Crypto & Blockchain":["COIN", "MSTR", "MARA", "RIOT", "HUT", "BTBT", "CLSK", "CIFR", "WULF"],
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
