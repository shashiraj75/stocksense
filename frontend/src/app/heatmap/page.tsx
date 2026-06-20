"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { api } from "@/utils/api";
import clsx from "clsx";
import { RefreshCw, Wifi } from "lucide-react";
import { IndexBar } from "@/components/IndexBar";
import { StockContextMenu } from "@/components/StockContextMenu";

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
    refetchInterval: 5 * 60_000,  // backend cache is 3min; poll at 5min to always hit cache
    staleTime: 4.5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 4,
    retryDelay: (attempt) => Math.min(10_000 * 2 ** attempt, 60_000), // 10s, 20s, 40s, 60s
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
          {/* Live status / loading badge — always visible */}
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
              ? "Fetching heatmap data…"
              : isFetching
                ? "Refreshing…"
                : lastUpdated
                  ? `Updated ${lastUpdated}`
                  : "Live"
            }
          </div>
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

      {/* Heatmap grid */}
      {(isLoading && !data) || (isError && !data) ? (
        <div className="space-y-4">
          {isError ? (
            <div className="bg-dark-card border border-dark-border rounded-xl p-10 text-center text-gray-500">
              <RefreshCw size={20} className="animate-spin mx-auto mb-3 text-gray-600" />
              <p className="text-sm">Server starting up · Retrying automatically…</p>
              <p className="text-xs mt-1 text-gray-600">This takes up to 30 seconds after inactivity.</p>
            </div>
          ) : (
            <div className="grid gap-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="bg-dark-card border border-dark-border rounded-xl p-4 animate-pulse h-28" />
              ))}
            </div>
          )}
        </div>
      ) : sectors.length === 0 ? (
        <div className="bg-dark-card border border-dark-border rounded-xl p-10 text-center text-gray-500">
          <p className="text-sm">Market closed · Loading last session data…</p>
          <p className="text-xs mt-1 text-gray-600">Data will appear automatically once retrieved.</p>
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

              {/* Stock tiles — grow to fill full width */}
              <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${sector.stocks.length}, minmax(0, 1fr))` }}>
                {sector.stocks.map(stock => (
                  <StockContextMenu key={stock.symbol} symbol={stock.symbol} market={market}>
                    <button
                      onClick={() => router.push(`/stock/${encodeURIComponent(stock.symbol)}?market=${market}`)}
                      title={stock.change_pct !== null ? `${stock.symbol}: ${stock.change_pct >= 0 ? "+" : ""}${stock.change_pct}% · Right-click for options` : `${stock.symbol}: no data`}
                      className={clsx(
                        "rounded-lg px-2 py-3 text-center w-full transition-opacity hover:opacity-75 active:scale-95 cursor-pointer",
                        getColor(stock.change_pct)
                      )}
                    >
                      <div className="text-xs font-bold font-mono leading-tight truncate">{stock.symbol}</div>
                      <div className="text-xs mt-0.5 font-medium tabular-nums">
                        {stock.change_pct !== null
                          ? `${stock.change_pct >= 0 ? "+" : ""}${stock.change_pct}%`
                          : "—"}
                      </div>
                    </button>
                  </StockContextMenu>
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
