"use client";
import { useState } from "react";

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

// Named BSE_MAP, not NSE — direct verification (see TradingViewWidget's
// module docstring below) found the `NSE:` exchange prefix returns a hard
// "This symbol is only available on TradingView" error from the widget
// embed for every Indian symbol tested, while `BSE:` resolves successfully.
// The old name ("NSE_OVERRIDE") was backwards: this table (and the
// `BSE:${up}` fallback below) is what makes Indian symbols work at all,
// not an override of some working NSE default.
const BSE_MAP: Record<string, string> = {
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

const OTHER_EXCHANGE: Record<string, string> = {
  "SPCX": "NASDAQ:SPCX",
  "SPAK": "NASDAQ:SPAK",
};

function getTVSymbol(symbol: string, market: "US" | "IN" | "CRYPTO" | "EU"): string {
  const up = symbol.toUpperCase();
  if (market === "CRYPTO") return CRYPTO_MAP[up] || `BINANCE:${up}USDT`;
  if (market === "IN")     return BSE_MAP[up] || `BSE:${up}`;
  if (OTHER_EXCHANGE[up])  return OTHER_EXCHANGE[up];
  if (NYSE_SYMBOLS.has(up)) return `NYSE:${up}`;
  return `NASDAQ:${up}`;
}

const INTERVALS_ALL = [
  { label: "1m",  value: "1"   },
  { label: "5m",  value: "5"   },
  { label: "15m", value: "15"  },
  { label: "1h",  value: "60"  },
  { label: "4h",  value: "240" },
  { label: "1D",  value: "D"   },
  { label: "1W",  value: "W"   },
  { label: "1M",  value: "M"   },
];

// Indian stocks are kept to D/W/M only. Directly verified (not assumed):
// the `NSE:` exchange prefix errors out entirely on TradingView's public
// widget embed for every symbol tested (KOTAKBANK, RELIANCE), so `BSE:` is
// the only viable prefix — but the anonymous/free embed couldn't be
// confirmed to render real intraday (1m/5m/15m/1h) candle data reliably
// for BSE either, only the quote header (which itself requires a live
// data subscription the free embed doesn't have). Rather than ship
// intraday buttons that may silently show an empty/broken chart for some
// or all Indian symbols, this stays conservative until reliably verified.
const INTERVALS_IN = [
  { label: "1D", value: "D" },
  { label: "1W", value: "W" },
  { label: "1M", value: "M" },
];

export function TradingViewWidget({ symbol, market, height = 420 }: Props) {
  const intervals = market === "IN" ? INTERVALS_IN : INTERVALS_ALL;
  const [interval, setInterval] = useState("D");
  const tvSymbol = getTVSymbol(symbol, market);

  const src =
    `https://www.tradingview.com/widgetembed/` +
    `?symbol=${encodeURIComponent(tvSymbol)}` +
    `&interval=${interval}` +
    `&hidesidetoolbar=0` +
    `&hidetoptoolbar=1` +
    `&symboledit=0` +
    `&saveimage=0` +
    `&toolbarbg=131722` +
    `&theme=dark` +
    `&style=1` +
    `&timezone=Etc%2FUTC` +
    `&withdateranges=1` +
    `&locale=en` +
    `&utm_source=stocksense`;

  return (
    <div className="w-full bg-[#131722] rounded-xl overflow-hidden">
      {/* Custom interval bar */}
      <div className="flex items-center gap-1 px-3 pt-3 pb-2">
        {intervals.map((iv) => (
          <button
            key={iv.value}
            onClick={() => setInterval(iv.value)}
            className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
              interval === iv.value
                ? "bg-blue-600 text-white"
                : "text-gray-400 hover:text-white hover:bg-white/10"
            }`}
          >
            {iv.label}
          </button>
        ))}
      </div>
      {market === "IN" && (
        <p className="px-3 pb-2 text-[11px] text-gray-500">
          Intraday chart intervals are unavailable for this Indian symbol in the embedded chart.
        </p>
      )}

      {/* Chart iframe — hidetoptoolbar=1 hides TV's broken interval row */}
      <iframe
        key={`${tvSymbol}-${interval}`}
        src={src}
        width="100%"
        height={height}
        frameBorder="0"
        allow="clipboard-read; clipboard-write"
        style={{ display: "block", border: "none" }}
        title={`${symbol} chart`}
      />
    </div>
  );
}
