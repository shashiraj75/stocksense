"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTopMovers } from "@/utils/api";
import { SignalBadge } from "@/components/SignalBadge";
import { TrendingUp, TrendingDown, Globe } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";

const POPULAR_US = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL"];
const POPULAR_IN = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "WIPRO"];

export default function Dashboard() {
  const [market, setMarket] = useState<"US" | "IN">("US");
  const { data: movers, isLoading } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market),
  });

  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="rounded-2xl bg-gradient-to-br from-brand-900 via-dark-card to-dark-bg border border-dark-border p-8">
        <div className="flex items-center gap-3 mb-3">
          <Globe size={28} className="text-brand-500" />
          <h1 className="text-3xl font-bold">StockSense</h1>
        </div>
        <p className="text-gray-400 text-lg max-w-xl">
          AI-powered predictions for US & Indian markets — short, medium, and long term signals backed by technicals, fundamentals & sentiment.
        </p>
      </div>

      {/* Market toggle */}
      <div className="flex items-center gap-3">
        <span className="text-gray-400 text-sm">Market:</span>
        {(["US", "IN"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMarket(m)}
            className={clsx(
              "px-4 py-1.5 rounded-lg text-sm font-medium transition-colors",
              market === m
                ? "bg-brand-500 text-white"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            )}
          >
            {m === "US" ? "🇺🇸 USA" : "🇮🇳 India"}
          </button>
        ))}
      </div>

      {/* Quick access */}
      <section>
        <h2 className="text-lg font-semibold mb-3 text-gray-300">Quick Access</h2>
        <div className="flex flex-wrap gap-2">
          {(market === "US" ? POPULAR_US : POPULAR_IN).map((sym) => (
            <Link
              key={sym}
              href={`/stock/${sym}?market=${market}`}
              className="px-4 py-2 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/60 text-sm font-mono font-bold text-white transition-colors"
            >
              {sym}
            </Link>
          ))}
        </div>
      </section>

      {/* Top movers */}
      <section>
        <h2 className="text-lg font-semibold mb-3 text-gray-300">Top Movers Today</h2>
        {isLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="h-24 rounded-xl bg-dark-card animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {movers?.movers.map((m) => (
              <Link
                key={m.symbol}
                href={`/stock/${m.symbol}?market=${market}`}
                className="p-4 rounded-xl bg-dark-card border border-dark-border hover:border-brand-500/50 transition-colors"
              >
                <p className="font-mono font-bold text-white">{m.symbol}</p>
                <p className="text-lg font-bold mt-1">
                  {market === "US" ? "$" : "₹"}{m.price.toLocaleString()}
                </p>
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
          { label: "Short Term", range: "1–10 Days", color: "border-bull/40", desc: "Technicals, momentum, volume, news sentiment" },
          { label: "Medium Term", range: "1–3 Months", color: "border-neutral/40", desc: "Earnings, sector rotation, macro trends" },
          { label: "Long Term", range: "6M – 3 Years", color: "border-brand-500/40", desc: "Fundamentals, management quality, government policy" },
        ].map((h) => (
          <div key={h.label} className={clsx("p-5 rounded-xl bg-dark-card border", h.color)}>
            <h3 className="font-bold text-white">{h.label}</h3>
            <p className="text-xs text-gray-400 mt-0.5">{h.range}</p>
            <p className="text-sm text-gray-300 mt-2">{h.desc}</p>
          </div>
        ))}
      </section>
    </div>
  );
}
