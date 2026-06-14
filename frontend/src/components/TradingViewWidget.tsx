"use client";

interface Props {
  symbol: string;
  market: "US" | "IN" | "CRYPTO" | "EU";
  height?: number;
}

const NYSE_SYMBOLS = new Set([
  "JPM","BAC","WFC","GS","MS","C","BRK.A","BRK.B","V","MA","AXP",
  "WMT","TGT","HD","LOW","KO","PEP","PG","JNJ","PFE","MRK","ABT",
  "UNH","CVX","XOM","BP","COP","SLB","BA","GE","CAT","DE","MMM",
  "UPS","FDX","DAL","UAL","AAL","LUV","T","VZ","BT","IBM","GM",
  "F","TM","MCD","NKE","DIS","MO","PM","CL","KMB","GIS","K","HSY",
  "BK","USB","PNC","MTB","RF","KEY","CFG","FITB","STI","ZION",
  "SPY","DIA","IWM","GLD","SLV","USO","XLE","XLF","XLK","XLV",
  "BLK","RTX","LMT","NOC","HON","EMR","ETN","PH","ROK","CMI",
  "SO","DUK","NEE","AEP","EXC","SRE","PCG","ED","FE","XEL",
  "AMT","PLD","CCI","EQIX","PSA","WY","AVB","EQR","MAA","UDR",
]);

const NSE_OVERRIDE: Record<string, string> = {
  "BAJFINANCE": "BSE:BAJFINANCE", "HDFCBANK": "BSE:HDFCBANK",
  "ICICIBANK": "BSE:ICICIBANK",   "KOTAKBANK": "BSE:KOTAKBANK",
  "AXISBANK": "BSE:AXISBANK",     "SBIN": "BSE:SBIN",
  "SBIBANK": "BSE:SBIN",          "RELIANCE": "BSE:RELIANCE",
  "TCS": "BSE:TCS",               "INFY": "BSE:INFY",
  "WIPRO": "BSE:WIPRO",           "HCLTECH": "BSE:HCLTECH",
  "TECHM": "BSE:TECHM",           "LTIM": "BSE:LTIM",
  "SUNPHARMA": "BSE:SUNPHARMA",   "DRREDDY": "BSE:DRREDDY",
  "CIPLA": "BSE:CIPLA",           "DIVISLAB": "BSE:DIVISLAB",
  "HINDUNILVR": "BSE:HINDUNILVR", "NESTLEIND": "BSE:NESTLEIND",
  "TITAN": "BSE:TITAN",           "ASIANPAINT": "BSE:ASIANPAINT",
  "MARUTI": "BSE:MARUTI",         "TATAMOTORS": "BSE:TATAMOTORS",
  "M&M": "BSE:500520",            "ADANIENT": "BSE:ADANIENT",
  "ADANIPORTS": "BSE:ADANIPORTS", "POWERGRID": "BSE:POWERGRID",
  "NTPC": "BSE:NTPC",             "ONGC": "BSE:ONGC",
  "COALINDIA": "BSE:COALINDIA",   "BAJAJFINSV": "BSE:BAJAJFINSV",
  "LTTS": "BSE:LTTS",             "NIFTY50": "NSE:NIFTY",
  "SENSEX": "BSE:SENSEX",
};

const CRYPTO_MAP: Record<string, string> = {
  "BTC": "BINANCE:BTCUSDT",  "ETH": "BINANCE:ETHUSDT",
  "BNB": "BINANCE:BNBUSDT",  "SOL": "BINANCE:SOLUSDT",
  "XRP": "BINANCE:XRPUSDT",  "ADA": "BINANCE:ADAUSDT",
  "DOGE": "BINANCE:DOGEUSDT","MATIC": "BINANCE:MATICUSDT",
  "DOT": "BINANCE:DOTUSDT",  "AVAX": "BINANCE:AVAXUSDT",
  "LINK": "BINANCE:LINKUSDT","UNI": "BINANCE:UNIUSDT",
  "LTC": "BINANCE:LTCUSDT",  "ATOM": "BINANCE:ATOMUSDT",
};

function getTVSymbol(symbol: string, market: "US" | "IN" | "CRYPTO" | "EU"): string {
  const up = symbol.toUpperCase();
  if (market === "CRYPTO") return CRYPTO_MAP[up] || `BINANCE:${up}USDT`;
  if (market === "IN")     return NSE_OVERRIDE[up] || `BSE:${up}`;
  if (NYSE_SYMBOLS.has(up)) return `NYSE:${up}`;
  return `NASDAQ:${up}`;
}

export function TradingViewWidget({ symbol, market, height = 450 }: Props) {
  const tvSymbol = getTVSymbol(symbol, market);

  // Use TradingView's widgetembed URL directly — no JS wrapper, no overlay divs
  const src =
    `https://www.tradingview.com/widgetembed/` +
    `?symbol=${encodeURIComponent(tvSymbol)}` +
    `&interval=D` +
    `&hidesidetoolbar=0` +
    `&hidetoptoolbar=0` +
    `&symboledit=0` +
    `&saveimage=0` +
    `&toolbarbg=1a1d2e` +
    `&theme=dark` +
    `&style=1` +
    `&timezone=Etc%2FUTC` +
    `&withdateranges=1` +
    `&locale=en` +
    `&utm_source=stocksense`;

  return (
    <iframe
      key={tvSymbol}
      src={src}
      width="100%"
      height={height}
      frameBorder="0"
      allow="clipboard-read; clipboard-write"
      style={{ display: "block", border: "none" }}
      title={`${symbol} chart`}
    />
  );
}
