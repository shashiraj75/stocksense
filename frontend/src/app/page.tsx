"use client";
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTopMovers, api } from "@/utils/api";
import { TrendingUp, TrendingDown, Globe } from "lucide-react";
import { LiveClock } from "@/components/LiveClock";
import Link from "next/link";
import clsx from "clsx";
import { IndexBar } from "@/components/IndexBar";
import { getMarketStatus } from "@/utils/marketHours";

const POPULAR_US     = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "JPM", "META", "AMZN"];
const POPULAR_IN     = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "WIPRO", "BAJFINANCE", "ICICIBANK", "ADANIENT"];
const POPULAR_CRYPTO = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE"];

// Static crypto "movers" — prices fetched on the stock page when clicked
const CRYPTO_CARDS = [
  { symbol: "BTC",  name: "Bitcoin" },
  { symbol: "ETH",  name: "Ethereum" },
  { symbol: "BNB",  name: "BNB" },
  { symbol: "SOL",  name: "Solana" },
  { symbol: "XRP",  name: "XRP" },
  { symbol: "DOGE", name: "Dogecoin" },
  { symbol: "ADA",  name: "Cardano" },
  { symbol: "AVAX", name: "Avalanche" },
  { symbol: "LINK", name: "Chainlink" },
  { symbol: "DOT",  name: "Polkadot" },
];

type DashMarket = "US" | "IN" | "CRYPTO";

const MARKET_TABS: { key: DashMarket; label: string }[] = [
  { key: "IN",     label: "🇮🇳 India" },
  { key: "US",     label: "🇺🇸 USA" },
  { key: "CRYPTO", label: "₿ Crypto" },
];

export default function Dashboard() {
  const [market, setMarket] = useState<DashMarket>("IN");

  const [marketStatus, setMarketStatus] = useState(() => getMarketStatus(market));
  useEffect(() => {
    const update = () => setMarketStatus(getMarketStatus(market));
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, [market]);

  const { data: movers, isLoading: moversLoading, dataUpdatedAt: moversUpdatedAt } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market as any),
    enabled: market !== "CRYPTO",
    refetchInterval: 60_000,   // backend caches 60s — no point polling faster
    staleTime: 55_000,
  });

  const { data: cryptoMovers, isLoading: cryptoLoading, dataUpdatedAt: cryptoUpdatedAt } = useQuery({
    queryKey: ["crypto-movers"],
    queryFn: () => api.get<{ movers: { symbol: string; name: string; price: number | null; change_pct: number }[] }>("/api/screener/crypto-movers").then(r => r.data),
    enabled: market === "CRYPTO",
    refetchInterval: 60_000,
    staleTime: 55_000,
  });

  const lastUpdated = market === "CRYPTO" ? cryptoUpdatedAt : moversUpdatedAt;

  const currency = market === "IN" ? "₹" : "$";
  const quickSymbols = market === "CRYPTO" ? POPULAR_CRYPTO : market === "IN" ? POPULAR_IN : POPULAR_US;

  return (
    <div className="space-y-6">

      {/* Header row: title + market tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div>
            <LiveClock inline />
            <div className="flex items-center gap-2">
              <Globe size={18} className="text-brand-500" />
              <h1 className="text-lg font-bold">StockSense</h1>
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          {MARKET_TABS.map(({ key, label }) => (
            <button key={key} onClick={() => setMarket(key)}
              className={clsx("px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                market === key
                  ? "bg-brand-500 text-white"
                  : "bg-dark-card border border-dark-border text-gray-400 hover:text-white")}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Live index bar */}
      {market !== "CRYPTO" && (
        <div className="bg-dark-card border border-dark-border rounded-xl px-4">
          <IndexBar market={market} />
        </div>
      )}

      {/* Horizon info cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {[
          { label: "Short Term",  period: "1–10 Days",      desc: "Technicals, momentum, volume, news sentiment",              border: "border-green-500/40",  text: "text-green-400" },
          { label: "Medium Term", period: "1–3 Months",     desc: "Earnings, sector rotation, macro trends",                   border: "border-yellow-500/40", text: "text-yellow-400" },
          { label: "Long Term",   period: "6M – 3 Years",   desc: "Fundamentals, management quality, government policy",       border: "border-purple-500/40", text: "text-purple-400" },
        ].map(({ label, period, desc, border, text }) => (
          <div key={label} className={`bg-dark-card border ${border} rounded-xl p-4`}>
            <p className={`text-sm font-bold ${text}`}>{label}</p>
            <p className="text-xs text-gray-500 mb-2">{period}</p>
            <p className="text-xs text-gray-300">{desc}</p>
          </div>
        ))}
      </div>

      {/* Quick Access */}
      <section>
        <h2 className="text-sm font-semibold mb-2 text-gray-400 uppercase tracking-wide">Quick Access</h2>
        <div className="flex flex-wrap gap-2">
          {quickSymbols.map((sym) => (
            <Link key={sym} href={`/stock/${sym}?market=${market}`}
              className="px-4 py-2 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/60 text-sm font-mono font-bold text-white transition-colors">
              {sym}
            </Link>
          ))}
        </div>
      </section>

      {/* Top Movers / Crypto grid */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
            {market === "CRYPTO" ? "Top Cryptocurrencies" : "Top Movers Today"}
          </h2>
          {lastUpdated > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="relative flex h-2 w-2">
                {marketStatus.isOpen && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                )}
                <span className={clsx("relative inline-flex rounded-full h-2 w-2", marketStatus.isOpen ? "bg-green-500" : "bg-red-500")}></span>
              </span>
              {marketStatus.isOpen ? "Live" : marketStatus.label}
              {marketStatus.nextEventLabel && (
                <span className="text-gray-600">· {marketStatus.nextEventLabel}</span>
              )}
            </div>
          )}
        </div>

        {market === "CRYPTO" ? (
          cryptoLoading ? (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {Array.from({ length: 10 }).map((_, i) => (
                <div key={i} className="h-24 rounded-xl bg-dark-card animate-pulse" />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              {(cryptoMovers?.movers ?? CRYPTO_CARDS).map((c) => (
                <Link key={c.symbol} href={`/stock/${c.symbol}?market=CRYPTO`}
                  className="p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors">
                  <p className="font-mono font-bold text-white">{c.symbol}</p>
                  <p className="text-xs text-gray-400 mt-0.5">{"name" in c ? (c as any).name : ""}</p>
                  {"price" in c && (c as any).price ? (
                    <>
                      <p className="text-base font-bold mt-1">
                        ${Number((c as any).price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </p>
                      <div className={clsx("flex items-center gap-1 text-xs font-medium mt-0.5",
                        (c as any).change_pct >= 0 ? "text-bull" : "text-bear")}>
                        {(c as any).change_pct >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                        {(c as any).change_pct >= 0 ? "+" : ""}{(c as any).change_pct}%
                      </div>
                    </>
                  ) : (
                    <p className="text-xs text-gray-500 mt-2">Loading…</p>
                  )}
                </Link>
              ))}
            </div>
          )
        ) : moversLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-dark-card animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {movers?.movers.map((m) => (
              <Link key={m.symbol} href={`/stock/${m.symbol}?market=${market}`}
                className="p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors">
                <p className="font-mono font-bold text-white">{m.symbol}</p>
                <p className="text-lg font-bold mt-1">{currency}{m.price.toLocaleString()}</p>
                <div className={clsx("flex items-center gap-1 text-sm font-medium mt-1",
                  m.change_pct >= 0 ? "text-bull" : "text-bear")}>
                  {m.change_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                  {m.change_pct >= 0 ? "+" : ""}{m.change_pct}%
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

    </div>
  );
}
