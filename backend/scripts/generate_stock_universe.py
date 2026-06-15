"""
One-time script to regenerate services/stock_universe.py
Sources:
  US  — Wikipedia S&P 500 + S&P 400 + S&P 600  (~1500 stocks)
  IN  — NSE public equity CSV                    (~2000 stocks)
  Extra US small-caps from NASDAQ screener CSV   (optional)

Run from the backend directory:
  python scripts/generate_stock_universe.py
"""

import io
import os
import sys
import textwrap
import requests
import pandas as pd

OUT_FILE = os.path.join(os.path.dirname(__file__), "../services/stock_universe.py")

HEADERS = {"User-Agent": "Mozilla/5.0 StockSense/1.0 (educational project)"}

# ── Major US ETFs (static — always included regardless of Wikipedia scrape) ───
US_ETFS = [
    ("SPY","SPDR S&P 500 ETF"),("QQQ","Invesco NASDAQ-100 ETF"),("IWM","iShares Russell 2000 ETF"),
    ("DIA","SPDR Dow Jones ETF"),("VTI","Vanguard Total Stock Market ETF"),("VOO","Vanguard S&P 500 ETF"),
    ("GLD","SPDR Gold Shares"),("SLV","iShares Silver Trust"),("USO","United States Oil Fund"),
    ("TLT","iShares 20+ Year Treasury Bond ETF"),("HYG","iShares High Yield Corporate Bond ETF"),
    ("LQD","iShares Investment Grade Corporate Bond ETF"),("BND","Vanguard Total Bond Market ETF"),
    ("XLE","Energy Select Sector SPDR"),("XLF","Financial Select Sector SPDR"),
    ("XLK","Technology Select Sector SPDR"),("XLV","Health Care Select Sector SPDR"),
    ("XLI","Industrial Select Sector SPDR"),("XLY","Consumer Discretionary Select Sector SPDR"),
    ("XLP","Consumer Staples Select Sector SPDR"),("XLU","Utilities Select Sector SPDR"),
    ("XLB","Materials Select Sector SPDR"),("XLRE","Real Estate Select Sector SPDR"),
    ("XLC","Communication Services Select Sector SPDR"),
    ("ARKK","ARK Innovation ETF"),("ARKG","ARK Genomic Revolution ETF"),
    ("ARKW","ARK Next Generation Internet ETF"),("ARKF","ARK Fintech Innovation ETF"),
    ("ARKQ","ARK Autonomous Technology & Robotics ETF"),
    ("SPAK","Defiance Next Gen SPAC ETF"),
    ("SOXX","iShares Semiconductor ETF"),("SMH","VanEck Semiconductor ETF"),
    ("JETS","US Global Jets ETF"),("HACK","ETFMG Prime Cyber Security ETF"),
    ("BOTZ","Global X Robotics & AI ETF"),("ROBO","Robo Global Robotics & Automation ETF"),
    ("FINX","Global X FinTech ETF"),("CLOU","Global X Cloud Computing ETF"),
    ("WCLD","WisdomTree Cloud Computing ETF"),("SKYY","First Trust Cloud Computing ETF"),
    ("CIBR","First Trust NASDAQ Cybersecurity ETF"),("BUG","Global X Cybersecurity ETF"),
    ("DRIV","Global X Autonomous & Electric Vehicles ETF"),("KARS","KraneShares Electric Vehicles ETF"),
    ("ICLN","iShares Global Clean Energy ETF"),("QCLN","First Trust NASDAQ Clean Edge Green Energy ETF"),
    ("TAN","Invesco Solar ETF"),("FAN","First Trust Global Wind Energy ETF"),
    ("KWEB","KraneShares CSI China Internet ETF"),("EEM","iShares MSCI Emerging Markets ETF"),
    ("EWJ","iShares MSCI Japan ETF"),("EWZ","iShares MSCI Brazil ETF"),("EWI","iShares MSCI India ETF"),
    ("VWO","Vanguard Emerging Markets ETF"),("INDA","iShares MSCI India ETF"),
    ("SOXS","Direxion Daily Semiconductor Bear 3x"),("SOXL","Direxion Daily Semiconductor Bull 3x"),
    ("TQQQ","ProShares UltraPro QQQ"),("SQQQ","ProShares UltraPro Short QQQ"),
    ("SPXL","Direxion Daily S&P 500 Bull 3x"),("SPXS","Direxion Daily S&P 500 Bear 3x"),
    ("UVXY","ProShares Ultra VIX Short-Term Futures ETF"),("VXX","iPath Series B S&P 500 VIX"),
]

# ── Crypto list (static — no API needed) ─────────────────────────────────────
CRYPTO_COINS = [
    ("BTC","Bitcoin"),("ETH","Ethereum"),("BNB","BNB"),("SOL","Solana"),
    ("XRP","XRP"),("ADA","Cardano"),("DOGE","Dogecoin"),("AVAX","Avalanche"),
    ("LINK","Chainlink"),("DOT","Polkadot"),("MATIC","Polygon"),("UNI","Uniswap"),
    ("LTC","Litecoin"),("ATOM","Cosmos"),("NEAR","NEAR Protocol"),("APT","Aptos"),
    ("ARB","Arbitrum"),("OP","Optimism"),("FTM","Fantom"),("ALGO","Algorand"),
    ("VET","VeChain"),("SAND","The Sandbox"),("MANA","Decentraland"),
    ("AXS","Axie Infinity"),("CRO","Cronos"),("SHIB","Shiba Inu"),
    ("PEPE","Pepe"),("FLOKI","Floki"),("WIF","Dogwifhat"),("BONK","Bonk"),
    ("SUI","Sui"),("SEI","Sei"),("TIA","Celestia"),("INJ","Injective"),
    ("IMX","Immutable X"),("LDO","Lido DAO"),("MKR","Maker"),("AAVE","Aave"),
    ("COMP","Compound"),("SNX","Synthetix"),("CRV","Curve DAO"),
    ("SUSHI","SushiSwap"),("1INCH","1inch"),("YFI","yearn.finance"),
    ("CAKE","PancakeSwap"),("APE","ApeCoin"),("GALA","Gala"),("CHZ","Chiliz"),
    ("ENJ","Enjin Coin"),("FLOW","Flow"),("THETA","Theta Network"),
    ("EOS","EOS"),("TRX","TRON"),("XLM","Stellar"),("XMR","Monero"),
    ("ZEC","Zcash"),("BCH","Bitcoin Cash"),("ETC","Ethereum Classic"),
    ("GRT","The Graph"),("FIL","Filecoin"),("AR","Arweave"),("HNT","Helium"),
    ("RNDR","Render"),("FET","Fetch.ai"),("AGIX","SingularityNET"),
    ("TAO","Bittensor"),("WLD","Worldcoin"),("PYTH","Pyth Network"),
    ("BLUR","Blur"),("GMT","STEPN"),("SAND","The Sandbox"),
]


def fetch_wikipedia_table(url: str, sym_hint: str = "ticker", name_hint: str = "company") -> pd.DataFrame | None:
    """Fetch a Wikipedia page and find the table that contains stock tickers."""
    try:
        resp = requests.get(url, headers={
            **HEADERS,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text), header=0)
        # Pick the table that actually has a ticker-like column
        for df in tables:
            df.columns = [c.strip() for c in df.columns]
            has_sym  = any(any(k in c.lower() for k in ("ticker","symbol")) for c in df.columns)
            has_name = any(any(k in c.lower() for k in ("security","company","name")) for c in df.columns)
            if has_sym and has_name:
                return df
        return tables[0]
    except Exception as e:
        print(f"✗  {e}")
        return None


def fetch_us_stocks() -> list[tuple[str, str]]:
    """Fetch S&P 500, 400, 600 and NASDAQ-100 from Wikipedia."""
    stocks: dict[str, str] = {}

    sources = [
        ("S&P 500",    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
        ("S&P 400",    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"),
        ("S&P 600",    "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"),
        ("NASDAQ-100", "https://en.wikipedia.org/wiki/Nasdaq-100#Changes_to_the_Nasdaq-100"),
    ]

    for label, url in sources:
        print(f"  Fetching {label} …", end=" ", flush=True)
        df = fetch_wikipedia_table(url)
        if df is None:
            continue
        df.columns = [c.strip() for c in df.columns]
        sym_col  = next((c for c in df.columns if any(k in c.lower() for k in ("ticker","symbol"))), None)
        name_col = next((c for c in df.columns if any(k in c.lower() for k in ("security","company","name"))), None)
        if not sym_col or not name_col:
            print(f"✗  cols not found: {list(df.columns)[:6]}")
            continue
        added = 0
        for _, row in df.iterrows():
            sym  = str(row[sym_col]).strip().replace(".", "-")
            name = str(row[name_col]).strip()
            if sym and name and sym != "nan" and len(sym) <= 6:
                if sym not in stocks:
                    stocks[sym] = name
                    added += 1
        print(f"✓  +{added} ({len(stocks)} total)")

    # Fallback: use NASDAQ public screener CSV if Wikipedia blocked
    if len(stocks) < 100:
        print("  Wikipedia blocked — fetching NASDAQ screener CSV …", end=" ", flush=True)
        try:
            url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=5000&exchange=nasdaq,nyse,amex&download=true"
            resp = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=30)
            data = resp.json()
            rows = data.get("data", {}).get("rows", [])
            for row in rows:
                sym  = str(row.get("symbol","")).strip()
                name = str(row.get("name","")).strip()
                if sym and name and len(sym) <= 6:
                    stocks[sym] = name
            print(f"✓  {len(rows)} rows")
        except Exception as e:
            print(f"✗  {e}")

    # Always include major ETFs
    for sym, name in US_ETFS:
        if sym not in stocks:
            stocks[sym] = name

    result = sorted(stocks.items(), key=lambda x: x[0])
    print(f"  → {len(result)} unique US stocks + ETFs")
    return result


def fetch_in_stocks() -> list[tuple[str, str]]:
    """Fetch all NSE-listed equities from NSE public CSV."""
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    stocks: dict[str, str] = {}

    try:
        print(f"  Fetching NSE equity list …", end=" ")
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = [c.strip() for c in df.columns]

        sym_col  = next((c for c in df.columns if c.strip().upper() in ("SYMBOL",)), None)
        name_col = next((c for c in df.columns if "name" in c.lower()), None)

        if not sym_col:
            sym_col  = df.columns[0]
        if not name_col:
            name_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        for _, row in df.iterrows():
            sym  = str(row[sym_col]).strip()
            name = str(row[name_col]).strip()
            if sym and name and sym != "nan" and len(sym) <= 20:
                stocks[sym] = name
        print(f"✓  {len(df)} rows")
    except Exception as e:
        print(f"✗  {e}")
        print("  Trying backup: NSE FnO list …", end=" ")
        try:
            url2 = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"
            resp2 = requests.get(url2, headers={**HEADERS, "Referer": "https://www.nseindia.com"}, timeout=30)
            data = resp2.json()
            for item in data.get("data", []):
                sym  = item.get("symbol", "").strip()
                name = item.get("meta", {}).get("companyName", sym)
                if sym:
                    stocks[sym] = name
            print(f"✓  {len(stocks)} stocks from FnO list")
        except Exception as e2:
            print(f"✗  {e2}")

    result = sorted(stocks.items(), key=lambda x: x[0])
    print(f"  → {len(result)} unique IN stocks total")
    return result


def format_list(name: str, items: list[tuple[str, str]], per_row: int = 2) -> str:
    """Format a list of (symbol, name) tuples as a Python list literal."""
    lines = [f"{name} = ["]
    for i in range(0, len(items), per_row):
        chunk = items[i:i + per_row]
        row = ",".join(f'("{s}","{n}")' for s, n in chunk)
        lines.append(f"    {row},")
    lines.append("]")
    return "\n".join(lines)


def write_universe(us: list, india: list, crypto: list):
    header = textwrap.dedent('''\
        """
        Static stock universe — auto-generated by scripts/generate_stock_universe.py
        US  : S&P 500 + S&P 400 + S&P 600 + NASDAQ-100
        IN  : All NSE-listed equities
        CRYPTO: Major coins (static)
        Do NOT edit manually — re-run the script to refresh.
        """

    ''')

    search_fn = textwrap.dedent('''\

        def search_universe(query: str, market: str, limit: int = 8) -> list:
            q = query.lower().strip()
            if not q:
                return []

            sources = []
            if market in ("US", "ALL"):
                sources.append(("US", US_STOCKS))
            if market in ("IN", "ALL"):
                sources.append(("IN", IN_STOCKS))
            if market in ("CRYPTO", "ALL"):
                sources.append(("CRYPTO", CRYPTO_COINS))

            # Priority: exact symbol > symbol starts-with > symbol contains > name starts-with > name contains
            exact, sym_start, sym_contain, name_start, name_contain = [], [], [], [], []
            seen: set[str] = set()

            for mkt, stocks in sources:
                for sym, name in stocks:
                    key = f"{sym}:{mkt}"
                    if key in seen:
                        continue
                    sl, nl = sym.lower().replace("-", "."), name.lower()
                    ql = q.replace("-", ".")
                    if sl == ql:
                        exact.append((sym, name, mkt))
                    elif sl.startswith(ql):
                        sym_start.append((sym, name, mkt))
                    elif ql in sl:
                        sym_contain.append((sym, name, mkt))
                    elif nl.startswith(q):
                        name_start.append((sym, name, mkt))
                    elif q in nl:
                        name_contain.append((sym, name, mkt))
                    else:
                        continue
                    seen.add(key)

            ordered = exact + sym_start + sym_contain + name_start + name_contain

            results = []
            for sym, name, mkt in ordered[:limit]:
                suffix = ".NS" if mkt == "IN" else ""
                results.append({"symbol": sym + suffix, "name": name, "market": mkt})
            return results
    ''')

    content = (
        header
        + format_list("US_STOCKS", us) + "\n\n"
        + format_list("IN_STOCKS", india) + "\n\n"
        + format_list("CRYPTO_COINS", crypto)
        + search_fn
    )

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n✅  Written to {OUT_FILE}")
    print(f"    US: {len(us)}  |  IN: {len(india)}  |  CRYPTO: {len(crypto)}")


def run() -> bool:
    """Fetch all sources and write stock_universe.py. Returns True on success."""
    try:
        print("[universe] Fetching US stocks …")
        us = fetch_us_stocks()
        print("[universe] Fetching Indian stocks …")
        india = fetch_in_stocks()
        if len(us) < 100 and len(india) < 100:
            print("[universe] Too few stocks fetched — skipping write to avoid overwriting good data.")
            return False
        write_universe(us, india, CRYPTO_COINS)
        return True
    except Exception as e:
        print(f"[universe] Error during refresh: {e}")
        return False


if __name__ == "__main__":
    print("=" * 55)
    print("StockSense — stock universe generator")
    print("=" * 55)
    success = run()
    if success:
        print("\nDone! Restart the backend to pick up the new list.")
    else:
        print("\nFailed — existing stock_universe.py unchanged.")
