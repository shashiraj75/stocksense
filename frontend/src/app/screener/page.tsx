"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchTopMovers, Market } from "@/utils/api";
import { TrendingUp, TrendingDown } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { StockContextMenu } from "@/components/StockContextMenu";

export default function ScreenerPage() {
  const [market, setMarket] = useState<Market>("IN");
  const { data, isLoading, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market),
    refetchInterval: 60_000,   // matches backend 60s TTL
    staleTime: 55_000,
    refetchOnWindowFocus: false,
  });

  const currency = market === "US" ? "$" : "₹";

  return (
    <div className="space-y-6">
      <MarketDisclaimer market={market} />
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Stock Screener</h1>
          <p className="text-gray-400 text-sm mt-1">Top movers across US and Indian markets</p>
        </div>
        {dataUpdatedAt > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-gray-500">
            {isFetching
              ? <span className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse inline-block" />
              : <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" />
                </span>
            }
            {isFetching ? "Refreshing…" : `Updated ${new Date(dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true })}`}
          </div>
        )}
      </div>

      <div className="flex items-center gap-3">
        {(["IN", "US"] as Market[]).map((m) => (
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
              : data?.movers.map((stock) => (
                  <StockContextMenu key={stock.symbol} symbol={stock.symbol} market={market} className="contents">
                    <tr className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                      <td className="px-3 sm:px-6 py-3 sm:py-4 font-mono font-bold text-white">{stock.symbol}</td>
                      <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">
                        {currency}{stock.price.toLocaleString()}
                      </td>
                      <td className={clsx("px-3 sm:px-6 py-3 sm:py-4 text-right font-medium",
                        stock.change_pct >= 0 ? "text-bull" : "text-bear")}>
                        <span className="flex items-center justify-end gap-1">
                          {stock.change_pct >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                          {stock.change_pct >= 0 ? "+" : ""}{stock.change_pct}%
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
