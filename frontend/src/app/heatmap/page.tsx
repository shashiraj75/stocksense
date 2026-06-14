"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import clsx from "clsx";

type Stock  = { symbol: string; change_pct: number | null };
type Sector = { sector: string; avg_change: number | null; stocks: Stock[] };

function getColor(pct: number | null): string {
  if (pct === null) return "bg-gray-700 text-gray-400";
  if (pct >= 3)  return "bg-green-500 text-white";
  if (pct >= 1)  return "bg-green-600/80 text-white";
  if (pct >= 0)  return "bg-green-900/60 text-green-300";
  if (pct >= -1) return "bg-red-900/60 text-red-300";
  if (pct >= -3) return "bg-red-600/80 text-white";
  return "bg-red-500 text-white";
}

function getSectorColor(pct: number | null): string {
  if (pct === null) return "border-gray-700 text-gray-400";
  if (pct >= 1)  return "border-green-500/60 text-green-400";
  if (pct >= 0)  return "border-green-700/40 text-green-600";
  if (pct >= -1) return "border-red-700/40 text-red-500";
  return "border-red-500/60 text-red-400";
}

export default function HeatmapPage() {
  const [market, setMarket] = useState<"IN" | "US">("IN");
  const router = useRouter();

  const { data, isLoading } = useQuery({
    queryKey: ["heatmap", market],
    queryFn: () => api.get<{ sectors: Sector[] }>(`/api/screener/heatmap?market=${market}`).then(r => r.data),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });

  const sectors = data?.sectors ?? [];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Market Heatmap</h1>
          <p className="text-sm text-gray-400 mt-1">Sector-wise performance — green = up, red = down</p>
        </div>
        <div className="flex gap-2">
          {(["IN", "US"] as const).map(m => (
            <button key={m} onClick={() => setMarket(m)}
              className={clsx("px-4 py-2 rounded-xl text-sm font-medium transition-colors border",
                market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-card border-dark-border text-gray-400 hover:text-white")}>
              {m === "IN" ? "🇮🇳 India" : "🇺🇸 USA"}
            </button>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 flex-wrap text-xs text-gray-400">
        <span>Change today:</span>
        {[
          { label: ">+3%",        cls: "bg-green-500" },
          { label: "+1% to +3%",  cls: "bg-green-600/80" },
          { label: "0% to +1%",   cls: "bg-green-900/60" },
          { label: "-1% to 0%",   cls: "bg-red-900/60" },
          { label: "-3% to -1%",  cls: "bg-red-600/80" },
          { label: "<-3%",        cls: "bg-red-500" },
        ].map(l => (
          <div key={l.label} className="flex items-center gap-1.5">
            <div className={clsx("w-3 h-3 rounded-sm", l.cls)} />
            <span>{l.label}</span>
          </div>
        ))}
      </div>

      {/* Heatmap grid */}
      {isLoading ? (
        <div className="grid gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-28" />
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          {sectors.map(sector => (
            <div key={sector.sector} className={clsx("bg-dark-card border rounded-xl p-4", getSectorColor(sector.avg_change))}>
              {/* Sector header */}
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-bold text-white text-sm">{sector.sector}</h2>
                <span className={clsx("text-sm font-bold", sector.avg_change === null ? "text-gray-500"
                  : sector.avg_change >= 0 ? "text-green-400" : "text-red-400")}>
                  {sector.avg_change !== null
                    ? `${sector.avg_change >= 0 ? "+" : ""}${sector.avg_change}%`
                    : "—"}
                </span>
              </div>

              {/* Stock tiles */}
              <div className="flex flex-wrap gap-2">
                {sector.stocks.map(stock => (
                  <button
                    key={stock.symbol}
                    onClick={() => router.push(`/stock/${stock.symbol}?market=${market}`)}
                    className={clsx(
                      "rounded-lg px-3 py-2 text-center min-w-[80px] transition-opacity hover:opacity-80 cursor-pointer",
                      getColor(stock.change_pct)
                    )}
                  >
                    <div className="text-xs font-bold font-mono">{stock.symbol}</div>
                    <div className="text-xs mt-0.5 font-medium">
                      {stock.change_pct !== null
                        ? `${stock.change_pct >= 0 ? "+" : ""}${stock.change_pct}%`
                        : "—"}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-gray-600 text-center">
        Data from Yahoo Finance · Refreshed every 3 minutes · Click any stock to view full analysis
      </p>
    </div>
  );
}
