"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import clsx from "clsx";
import { RefreshCw, AlertCircle } from "lucide-react";
import { IndexBar } from "@/components/IndexBar";

type Stock  = { symbol: string; change_pct: number | null };
type Sector = { sector: string; avg_change: number | null; stocks: Stock[]; loaded: number; total: number };

function getColor(pct: number | null): string {
  if (pct === null) return "bg-gray-800 text-gray-500 border border-gray-700";
  if (pct >= 3)  return "bg-green-500 text-white";
  if (pct >= 1)  return "bg-green-600/80 text-white";
  if (pct >= 0)  return "bg-green-900/60 text-green-300";
  if (pct >= -1) return "bg-red-900/60 text-red-300";
  if (pct >= -3) return "bg-red-600/80 text-white";
  return "bg-red-500 text-white";
}

function getSectorColor(pct: number | null): string {
  if (pct === null) return "border-gray-700";
  if (pct >= 3)  return "border-green-400/80";
  if (pct >= 1)  return "border-green-500/60";
  if (pct >= 0)  return "border-green-700/40";
  if (pct >= -1) return "border-red-700/40";
  if (pct >= -3) return "border-red-500/60";
  return "border-red-400/80";
}

export default function HeatmapPage() {
  const [market, setMarket] = useState<"IN" | "US">("IN");
  const router = useRouter();

  const { data, isLoading, isFetching, isError, dataUpdatedAt } = useQuery({
    queryKey: ["heatmap", market],
    queryFn: () => api.get<{ sectors: Sector[] }>(`/api/screener/heatmap?market=${market}`).then(r => r.data),
    refetchInterval: 3 * 60_000,
    staleTime: 2.5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const sectors = [...(data?.sectors ?? [])]
    .sort((a, b) => (b.avg_change ?? -Infinity) - (a.avg_change ?? -Infinity))
    .map(s => ({ ...s, stocks: [...s.stocks].sort((a, b) => (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity)) }));

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("en-IN", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
      })
    : null;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Market Heatmap</h1>
          <p className="text-sm text-gray-400 mt-1">Sector-wise performance — green = up, red = down</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Last updated badge */}
          {lastUpdated && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500 bg-dark-card border border-dark-border rounded-lg px-3 py-1.5">
              {isFetching
                ? <RefreshCw size={11} className="animate-spin text-brand-500" />
                : <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
              }
              {isFetching ? "Refreshing…" : `Updated ${lastUpdated}`}
            </div>
          )}
          {/* Market toggle */}
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
      </div>

      {/* Live index bar */}
      <div className="bg-dark-card border border-dark-border rounded-xl px-4">
        <IndexBar market={market} />
      </div>

      {/* Legend */}
      <div className="flex items-center gap-3 flex-wrap text-xs text-gray-400">
        <span>Change today:</span>
        {[
          { label: ">+3%",       cls: "bg-green-500" },
          { label: "+1% to +3%", cls: "bg-green-600/80" },
          { label: "0% to +1%",  cls: "bg-green-900/60" },
          { label: "-1% to 0%",  cls: "bg-red-900/60" },
          { label: "-3% to -1%", cls: "bg-red-600/80" },
          { label: "<-3%",       cls: "bg-red-500" },
          { label: "No data",    cls: "bg-gray-800 border border-gray-700" },
        ].map(l => (
          <div key={l.label} className="flex items-center gap-1.5">
            <div className={clsx("w-3 h-3 rounded-sm", l.cls)} />
            <span>{l.label}</span>
          </div>
        ))}
      </div>

      {/* Error state */}
      {isError && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-4 flex items-center gap-3">
          <AlertCircle size={18} className="text-red-400 shrink-0" />
          <p className="text-sm text-red-300">
            Failed to load heatmap data. The server may be starting up — please wait a moment and refresh.
          </p>
        </div>
      )}

      {/* Heatmap grid */}
      {(isLoading && !data) ? (
        <div className="grid gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-28" />
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          {sectors.map(sector => (
            <div key={sector.sector} className={clsx("bg-dark-card border-2 rounded-xl p-4", getSectorColor(sector.avg_change))}>
              {/* Sector header */}
              <div className="flex items-center justify-between mb-3 gap-2">
                <div className="flex items-center gap-2">
                  <h2 className="font-bold text-white text-sm">{sector.sector}</h2>
                  {/* Data completeness */}
                  {sector.loaded < sector.total && (
                    <span className="text-xs text-gray-500 bg-dark-border/60 px-1.5 py-0.5 rounded">
                      {sector.loaded}/{sector.total} loaded
                    </span>
                  )}
                </div>
                <span className={clsx("text-sm font-bold tabular-nums",
                  sector.avg_change === null ? "text-gray-500"
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
                    title={stock.change_pct !== null ? `${stock.symbol}: ${stock.change_pct >= 0 ? "+" : ""}${stock.change_pct}%` : `${stock.symbol}: no data`}
                    className={clsx(
                      "rounded-lg px-3 py-2 text-center min-w-[80px] transition-opacity hover:opacity-75 active:scale-95 cursor-pointer",
                      getColor(stock.change_pct)
                    )}
                  >
                    <div className="text-xs font-bold font-mono leading-tight">{stock.symbol}</div>
                    <div className="text-xs mt-0.5 font-medium tabular-nums">
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
        Data from Yahoo Finance · Cached 3 min server-side · Gray tiles = data unavailable for this symbol · Click any stock for full analysis
      </p>
    </div>
  );
}
