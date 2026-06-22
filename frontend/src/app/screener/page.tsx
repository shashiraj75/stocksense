"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTopMovers, Market } from "@/utils/api";
import { TrendingUp, TrendingDown, RefreshCw, Wifi } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { StockContextMenu } from "@/components/StockContextMenu";

export default function ScreenerPage() {
  const [market, setMarket] = useState<Market>("IN");
  const { data, isLoading, isFetching, isError, dataUpdatedAt } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market),
    refetchInterval: 60_000,   // matches backend 60s TTL
    staleTime: 55_000,
    refetchOnWindowFocus: false,
    retry: 4,
    retryDelay: (attempt) => Math.min(10_000 * 2 ** attempt, 60_000),
  });

  const currency = market === "US" ? "$" : "₹";
  const lastUpdated = dataUpdatedAt > 0
    ? new Date(dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })
    : null;

  return (
    <div className="space-y-6">
      <MarketDisclaimer market={market} />

      {/* Header — layout matches Market Heatmap / Market Overview header style */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Stock Screener</h1>
          <p className="text-sm text-gray-400 mt-1">Top movers across US and Indian markets</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Live status / loading badge */}
          <div className={clsx(
            "flex items-center gap-1.5 text-xs rounded-lg px-3 py-1.5 border transition-all",
            isLoading && !data
              ? "bg-brand-500/10 border-brand-500/30 text-brand-400"
              : isFetching
                ? "bg-yellow-500/10 border-yellow-500/30 text-yellow-400"
                : "bg-dark-card border-dark-border text-gray-500"
          )}>
            {(isLoading && !data) || isFetching
              ? <RefreshCw size={11} className="animate-spin" />
              : <Wifi size={11} className="text-green-500" />
            }
            {isLoading && !data
              ? "Fetching screener data…"
              : isFetching
                ? "Refreshing…"
                : lastUpdated
                  ? `Updated ${lastUpdated}`
                  : "Live"
            }
          </div>
          {/* Market toggle */}
          <div className="flex gap-2">
            {(["IN", "US"] as Market[]).map((m) => (
              <button key={m} onClick={() => setMarket(m)}
                className={clsx("px-4 py-2 rounded-xl text-sm font-medium transition-colors border",
                  market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
                {m === "IN" ? "🇮🇳 India" : "🇺🇸 USA"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {(data as any)?.stale && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-2.5 text-xs text-amber-300">
          Market closed · Showing last session data
        </div>
      )}

      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-x-auto">
        <table className="w-full text-sm min-w-[320px]">
          <thead>
            <tr className="border-b border-dark-border text-gray-400 text-left">
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium">Symbol</th>
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">Price</th>
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">Change</th>
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {isLoading
              ? Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b border-dark-border">
                    <td colSpan={4} className="px-3 sm:px-6 py-3 sm:py-4">
                      <div className="h-4 bg-dark-border rounded animate-pulse" />
                    </td>
                  </tr>
                ))
              : isError
              ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-12 text-center text-gray-500 text-sm">
                      Server starting up · Retrying automatically…
                    </td>
                  </tr>
                )
              : (!data?.movers?.length)
              ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-12 text-center text-gray-500 text-sm">
                      Market is closed — last session data will appear shortly.
                    </td>
                  </tr>
                )
              : data?.movers.map((stock) => (
                  <StockContextMenu key={stock.symbol} symbol={stock.symbol} market={market} className="contents">
                    <tr className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                      <td className="px-3 sm:px-6 py-3 sm:py-4 font-mono font-bold text-white">{stock.symbol}</td>
                      <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">
                        {currency}{stock.price?.toLocaleString() ?? "—"}
                      </td>
                      <td className={clsx("px-3 sm:px-6 py-3 sm:py-4 text-right font-medium",
                        (stock.change_pct ?? 0) >= 0 ? "text-bull" : "text-bear")}>
                        <span className="flex items-center justify-end gap-1">
                          {(stock.change_pct ?? 0) >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                          {(stock.change_pct ?? 0) >= 0 ? "+" : ""}{stock.change_pct ?? 0}%
                        </span>
                      </td>
                      <td className="px-3 sm:px-6 py-3 sm:py-4 text-right">
                        <Link
                          href={`/stock/${stock.symbol}?market=${market}`}
                          className="px-3 py-1 rounded-lg bg-brand-500/20 text-brand-500 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium transition-colors"
                        >
                          Analyse →
                        </Link>
                      </td>
                    </tr>
                  </StockContextMenu>
                ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
