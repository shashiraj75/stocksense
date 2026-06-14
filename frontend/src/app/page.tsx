"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTopMovers, api } from "@/utils/api";
import { TrendingUp, TrendingDown, Globe } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";

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
  { key: "US",     label: "🇺🇸 USA" },
  { key: "IN",     label: "🇮🇳 India" },
  { key: "CRYPTO", label: "₿ Crypto" },
];

export default function Dashboard() {
  const [market, setMarket] = useState<DashMarket>("US");

  const { data: movers, isLoading: moversLoading } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market as any),
    enabled: market !== "CRYPTO",
    staleTime: 5 * 60_000,
  });

  const { data: cryptoMovers, isLoading: cryptoLoading } = useQuery({
    queryKey: ["crypto-movers"],
    queryFn: () => api.get<{ movers: { symbol: string; name: string; price: number | null; change_pct: number }[] }>("/api/screener/crypto-movers").then(r => r.data),
    enabled: market === "CRYPTO",
    staleTime: 2 * 60_000,
  });

  const currency = market === "IN" ? "₹" : "$";
  const quickSymbols = market === "CRYPTO" ? POPULAR_CRYPTO : market === "IN" ? POPULAR_IN : POPULAR_US;

  return (
    <div className="space-y-6">

      {/* Header row: title + market tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Globe size={18} className="text-brand-500" />
          <h1 className="text-lg font-bold">StockSense</h1>
          <span className="text-gray-500 text-sm hidden sm:inline">
            — AI predictions for stocks &amp; crypto
          </span>
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
        <h2 className="text-sm font-semibold mb-3 text-gray-400 uppercase tracking-wide">
          {market === "CRYPTO" ? "Top Cryptocurrencies" : "Top Movers Today"}
        </h2>

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

      {/* Horizons explainer */}
      <section className="grid md:grid-cols-3 gap-4">
        {[
          { label: "Short Term",  range: "1–10 Days",    color: "border-bull/40",      desc: "Technicals, momentum, volume, news sentiment" },
          { label: "Medium Term", range: "1–3 Months",   color: "border-neutral/40",   desc: "Earnings, sector rotation, macro trends" },
          { label: "Long Term",   range: "6M – 3 Years", color: "border-brand-500/40", desc: "Fundamentals, management quality, government policy" },
        ].map((h) => (
          <div key={h.label} className={clsx("p-4 rounded-xl bg-dark-card border", h.color)}>
            <h3 className="font-bold text-white text-sm">{h.label}</h3>
            <p className="text-xs text-gray-400 mt-0.5">{h.range}</p>
            <p className="text-sm text-gray-300 mt-1.5">{h.desc}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
