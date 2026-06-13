"use client";
import { useEffect, useRef } from "react";

interface Props {
  symbol: string;
  market: "US" | "IN";
  height?: number;
}

// Stocks listed on NYSE rather than NASDAQ
const NYSE_SYMBOLS = new Set([
  "JPM","BAC","WFC","GS","MS","C","BRK.A","BRK.B","V","MA","AXP",
  "WMT","TGT","HD","LOW","KO","PEP","PG","JNJ","PFE","MRK","ABT",
  "UNH","CVX","XOM","BP","COP","SLB","BA","GE","CAT","DE","MMM",
  "UPS","FDX","DAL","UAL","AAL","LUV","T","VZ","BT","IBM","GM",
  "F","TM","MCD","NKE","DIS","MO","PM","CL","KMB","GIS","K","HSY",
  "BK","USB","PNC","MTB","RF","KEY","CFG","FITB","STI","ZION",
  "SPY","DIA","IWM","GLD","SLV","USO","XLE","XLF","XLK","XLV",
]);

function getTVSymbol(symbol: string, market: "US" | "IN"): string {
  if (market === "IN") return `NSE:${symbol}`;
  if (NYSE_SYMBOLS.has(symbol.toUpperCase())) return `NYSE:${symbol}`;
  // Default to NASDAQ for tech stocks; TradingView will show error if wrong
  return `NASDAQ:${symbol}`;
}

// Free TradingView widget — no account or API key needed
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
      style: "1",
      locale: "en",
      enable_publishing: false,
      allow_symbol_change: false,
      calendar: false,
      support_host: "https://www.tradingview.com",
    });

    const wrapper = document.createElement("div");
    wrapper.className = "tradingview-widget-container__widget";
    wrapper.style.height = `${height - 32}px`;
    wrapper.style.width = "100%";

    containerRef.current.appendChild(wrapper);
    containerRef.current.appendChild(script);

    return () => {
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [tvSymbol, height]);

  return (
    <div
      className="tradingview-widget-container w-full rounded-xl overflow-hidden"
      style={{ height }}
      ref={containerRef}
    />
  );
}
