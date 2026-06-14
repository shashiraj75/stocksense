"use client";
import { useEffect, useRef } from "react";

interface Props {
  symbol: string;
  market: "US" | "IN" | "CRYPTO" | "EU";
  height?: number;
}

// NYSE-listed US stocks (not NASDAQ)
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

// NSE India symbols that TradingView maps differently
// BSE prefix is more reliably resolved by TradingView's free widget for Indian stocks
const NSE_OVERRIDE: Record<string, string> = {
  "BAJFINANCE": "BSE:BAJFINANCE",
  "HDFCBANK":   "BSE:HDFCBANK",
  "ICICIBANK":  "BSE:ICICIBANK",
  "KOTAKBANK":  "BSE:KOTAKBANK",
  "AXISBANK":   "BSE:AXISBANK",
  "SBIBANK":    "BSE:SBIN",
  "SBIN":       "BSE:SBIN",
  "RELIANCE":   "BSE:RELIANCE",
  "TCS":        "BSE:TCS",
  "INFY":       "BSE:INFY",
  "WIPRO":      "BSE:WIPRO",
  "HCLTECH":    "BSE:HCLTECH",
  "TECHM":      "BSE:TECHM",
  "LTIM":       "BSE:LTIM",
  "SUNPHARMA":  "BSE:SUNPHARMA",
  "DRREDDY":    "BSE:DRREDDY",
  "CIPLA":      "BSE:CIPLA",
  "DIVISLAB":   "BSE:DIVISLAB",
  "HINDUNILVR": "BSE:HINDUNILVR",
  "NESTLEIND":  "BSE:NESTLEIND",
  "TITAN":      "BSE:TITAN",
  "ASIANPAINT": "BSE:ASIANPAINT",
  "MARUTI":     "BSE:MARUTI",
  "TATAMOTORS": "BSE:TATAMOTORS",
  "M&M":        "BSE:500520",
  "ADANIENT":   "BSE:ADANIENT",
  "ADANIPORTS": "BSE:ADANIPORTS",
  "POWERGRID":  "BSE:POWERGRID",
  "NTPC":       "BSE:NTPC",
  "ONGC":       "BSE:ONGC",
  "COALINDIA":  "BSE:COALINDIA",
  "BAJAJFINSV": "BSE:BAJAJFINSV",
  "LTTS":       "BSE:LTTS",
  "NIFTY50":    "NSE:NIFTY",
  "SENSEX":     "BSE:SENSEX",
};

// Crypto symbols → Binance or COINBASE
const CRYPTO_MAP: Record<string, string> = {
  "BTC":  "BINANCE:BTCUSDT",
  "ETH":  "BINANCE:ETHUSDT",
  "BNB":  "BINANCE:BNBUSDT",
  "SOL":  "BINANCE:SOLUSDT",
  "XRP":  "BINANCE:XRPUSDT",
  "ADA":  "BINANCE:ADAUSDT",
  "DOGE": "BINANCE:DOGEUSDT",
  "MATIC":"BINANCE:MATICUSDT",
  "DOT":  "BINANCE:DOTUSDT",
  "AVAX": "BINANCE:AVAXUSDT",
  "LINK": "BINANCE:LINKUSDT",
  "UNI":  "BINANCE:UNIUSDT",
  "LTC":  "BINANCE:LTCUSDT",
  "ATOM": "BINANCE:ATOMUSDT",
};

// European stocks → prefix with exchange
const EU_MAP: Record<string, string> = {
  "SAP":   "XETR:SAP",
  "ASML":  "NASDAQ:ASML",
  "SHELL": "LSE:SHEL",
  "LVMH":  "EURONEXT:MC",
  "NESN":  "SIX:NESN",
  "NOVO":  "CPH:NOVO_B",
  "ARM":   "NASDAQ:ARM",
};

function getTVSymbol(symbol: string, market: "US" | "IN" | "CRYPTO" | "EU"): string {
  const up = symbol.toUpperCase();
  if (market === "CRYPTO") return CRYPTO_MAP[up] || `BINANCE:${up}USDT`;
  if (market === "EU")     return EU_MAP[up]     || `XETR:${up}`;
  if (market === "IN")     return NSE_OVERRIDE[up] || `BSE:${up}`;
  if (NYSE_SYMBOLS.has(up)) return `NYSE:${up}`;
  return `NASDAQ:${up}`;
}

export function TradingViewWidget({ symbol, market, height = 450 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tvSymbol = getTVSymbol(symbol, market);

  useEffect(() => {
    if (!containerRef.current) return;
    containerRef.current.innerHTML = "";

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol: tvSymbol,
      interval: "D",
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",           // 1 = Candles
      locale: "en",
      enable_publishing: false,
      allow_symbol_change: false,
      hide_top_toolbar: false,
      hide_legend: false,
      save_image: false,
      calendar: false,
      support_host: "https://www.tradingview.com",
    });

    const wrapper = document.createElement("div");
    wrapper.className = "tradingview-widget-container__widget";
    wrapper.style.height = "100%";
    wrapper.style.width = "100%";

    containerRef.current.appendChild(wrapper);
    containerRef.current.appendChild(script);

    return () => { if (containerRef.current) containerRef.current.innerHTML = ""; };
  }, [tvSymbol, height]);

  return (
    <div className="tradingview-widget-container w-full rounded-xl" style={{ height }} ref={containerRef} />
  );
}
