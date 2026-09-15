"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, fetchTopMovers, Market } from "@/utils/api";
import { TrendingUp, TrendingDown, RefreshCw, Wifi, Filter, SlidersHorizontal, Bookmark, Trash2, Save } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { StockContextMenu } from "@/components/StockContextMenu";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { UnsupportedMarketNotice } from "@/components/UnsupportedMarketNotice";
import { useAuth } from "@/lib/AuthContext";

// ── Advanced filter types ────────────────────────────────────────────────────
// Field names/shape mirror api/routers/screener.py's SavedScreenFilters
// exactly — every key here is one query param the backend's /filter and
// /saved endpoints both accept.
interface ScreenFilters {
  sector?: string;
  min_market_cap?: number;
  max_market_cap?: number;
  max_pe?: number;
  min_roe?: number;
  min_roce?: number;
  max_debt_to_equity?: number;
  min_sales_growth_3y?: number;
  min_profit_growth_3y?: number;
  min_business_quality_score?: number;
}

interface FilteredStock {
  symbol: string; market: string; company_name: string | null; sector_name: string | null;
  market_cap_cr: number | null; market_cap_usd_m: number | null; pe_ratio: number | null;
  roe_pct: number | null; roce_pct: number | null; debt_to_equity_pct: number | null;
  sales_growth_3y_pct: number | null; profit_growth_3y_pct: number | null;
  business_quality_score: number | null; business_quality_grade: string | null;
}

interface SavedScreen { id: number; name: string; market: Market; filters: ScreenFilters; created_at: string | null; }

const EMPTY_FILTERS: ScreenFilters = {};

// Strips undefined/empty-string/NaN values so the request (and a saved
// screen's own stored payload) only ever contains criteria the user
// actually set — an empty form must never accidentally apply every
// filter as "0" or "".
function cleanFilters(f: ScreenFilters): ScreenFilters {
  const out: ScreenFilters = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === undefined || v === "" || (typeof v === "number" && Number.isNaN(v))) continue;
    (out as any)[k] = v;
  }
  return out;
}

function NumberField({ label, value, onChange, placeholder }: {
  label: string; value: number | undefined; onChange: (v: number | undefined) => void; placeholder?: string;
}) {
  return (
    <label className="flex flex-col gap-1 min-w-0">
      <span className="text-[11px] text-gray-500">{label}</span>
      <input
        type="number"
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
        className="bg-dark-bg border border-dark-border rounded-lg px-2.5 py-1.5 text-sm text-white outline-none focus:border-brand-500 transition-colors"
      />
    </label>
  );
}

export default function ScreenerPage() {
  const [market] = useMarketPreference(["IN", "US"] as const, "IN");
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const qc = useQueryClient();

  const [mode, setMode] = useState<"movers" | "filter">("movers");
  const [filters, setFilters] = useState<ScreenFilters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<ScreenFilters | null>(null);
  const [saveName, setSaveName] = useState("");
  const [showSaveBox, setShowSaveBox] = useState(false);

  // ── Top Movers (unchanged) ─────────────────────────────────────────────────
  const { data, isLoading, isFetching, isError, dataUpdatedAt } = useQuery({
    queryKey: ["movers", market],
    queryFn: () => fetchTopMovers(market),
    refetchInterval: 60_000,
    staleTime: 55_000,
    refetchOnWindowFocus: false,
    retry: 4,
    retryDelay: (attempt) => Math.min(10_000 * 2 ** attempt, 60_000),
    enabled: mode === "movers",
  });

  // ── Advanced filter ─────────────────────────────────────────────────────────
  const filterQuery = useQuery({
    queryKey: ["screener-filter", market, appliedFilters],
    queryFn: () =>
      api
        .get<{ market: string; results: FilteredStock[]; last_refreshed: string | null; error?: string }>(
          "/api/screener/filter",
          { params: { market, ...appliedFilters } },
        )
        .then((r) => r.data),
    enabled: mode === "filter" && appliedFilters !== null,
    staleTime: 5 * 60_000,
  });

  // ── Saved screens ────────────────────────────────────────────────────────────
  const savedQuery = useQuery({
    queryKey: ["saved-screens", userId],
    queryFn: () => api.get<{ screens: SavedScreen[] }>(`/api/screener/saved/${userId}`).then((r) => r.data),
    enabled: !!userId,
    staleTime: 60_000,
  });

  const saveMutation = useMutation({
    mutationFn: (name: string) =>
      api.post(`/api/screener/saved/${userId}`, { name, market, filters: cleanFilters(filters) }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["saved-screens", userId] });
      setShowSaveBox(false);
      setSaveName("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.delete(`/api/screener/saved/${userId}/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved-screens", userId] }),
  });

  const currency = market === "US" ? "$" : "₹";
  const lastUpdated = dataUpdatedAt > 0
    ? new Date(dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })
    : null;

  const applyFilters = () => setAppliedFilters(cleanFilters(filters));
  const loadSavedScreen = (s: SavedScreen) => {
    setFilters(s.filters);
    setAppliedFilters(cleanFilters(s.filters));
    setMode("filter");
  };
  const hasAnyFilter = Object.keys(cleanFilters(filters)).length > 0;

  return (
    <div className="space-y-6">
      <UnsupportedMarketNotice supported={["IN", "US"]} />
      <MarketDisclaimer market={market} />

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <Filter size={22} className="text-brand-500" />
          <div>
            <h1 className="text-2xl font-bold">Stock Screener</h1>
            <p className="text-sm text-gray-400 mt-1">
              {mode === "movers" ? "Top movers across US and Indian markets" : "Filter by sector, valuation, quality & growth"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 ml-auto">
          {mode === "movers" && (
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
                : <Wifi size={11} className="text-green-500" />}
              {isLoading && !data ? "Fetching screener data…" : isFetching ? "Refreshing…" : lastUpdated ? `Updated ${lastUpdated}` : "Live"}
            </div>
          )}
          <div className="flex items-center gap-0.5 bg-dark-bg border border-dark-border rounded-lg p-0.5">
            <button
              onClick={() => setMode("movers")}
              className={clsx("px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                mode === "movers" ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white")}
            >
              Top Movers
            </button>
            <button
              onClick={() => setMode("filter")}
              className={clsx("flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                mode === "filter" ? "bg-brand-500 text-white" : "text-gray-400 hover:text-white")}
            >
              <SlidersHorizontal size={12} /> Advanced Filter
            </button>
          </div>
        </div>
      </div>

      {mode === "movers" && (data as any)?.stale && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-2.5 text-xs text-amber-300">
          Market closed · Showing last session data
        </div>
      )}

      {mode === "filter" && (
        <>
          {/* Saved screens */}
          {userId && (
            <div className="flex items-center gap-2 flex-wrap">
              {(savedQuery.data?.screens ?? []).map((s) => (
                <div key={s.id} className="flex items-center gap-1.5 bg-dark-card border border-dark-border rounded-lg px-2.5 py-1.5 text-xs">
                  <button onClick={() => loadSavedScreen(s)} className="flex items-center gap-1.5 text-gray-300 hover:text-white">
                    <Bookmark size={11} className="text-brand-400" /> {s.name}
                  </button>
                  <button onClick={() => deleteMutation.mutate(s.id)} className="text-gray-600 hover:text-red-400" title="Delete saved screen">
                    <Trash2 size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Filter form */}
          <div className="bg-dark-card border border-dark-border rounded-2xl p-4 space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              <label className="flex flex-col gap-1 min-w-0">
                <span className="text-[11px] text-gray-500">Sector</span>
                <input
                  type="text" value={filters.sector ?? ""} placeholder="e.g. IT, Pharma…"
                  onChange={(e) => setFilters((f) => ({ ...f, sector: e.target.value }))}
                  className="bg-dark-bg border border-dark-border rounded-lg px-2.5 py-1.5 text-sm text-white outline-none focus:border-brand-500 transition-colors"
                />
              </label>
              <NumberField label={`Min Market Cap (${market === "IN" ? "₹ Cr" : "$M"})`} value={filters.min_market_cap} onChange={(v) => setFilters((f) => ({ ...f, min_market_cap: v }))} />
              <NumberField label="Max P/E" value={filters.max_pe} onChange={(v) => setFilters((f) => ({ ...f, max_pe: v }))} />
              <NumberField label="Min ROE %" value={filters.min_roe} onChange={(v) => setFilters((f) => ({ ...f, min_roe: v }))} />
              <NumberField label="Min ROCE %" value={filters.min_roce} onChange={(v) => setFilters((f) => ({ ...f, min_roce: v }))} />
              <NumberField label="Max Debt/Equity %" value={filters.max_debt_to_equity} onChange={(v) => setFilters((f) => ({ ...f, max_debt_to_equity: v }))} />
              <NumberField label="Min 3Y Sales Growth %" value={filters.min_sales_growth_3y} onChange={(v) => setFilters((f) => ({ ...f, min_sales_growth_3y: v }))} />
              <NumberField label="Min 3Y Profit Growth %" value={filters.min_profit_growth_3y} onChange={(v) => setFilters((f) => ({ ...f, min_profit_growth_3y: v }))} />
              <NumberField label="Min Quality Score" value={filters.min_business_quality_score} onChange={(v) => setFilters((f) => ({ ...f, min_business_quality_score: v }))} placeholder="0–100" />
            </div>
            <div className="flex items-center gap-2 flex-wrap pt-1">
              <button onClick={applyFilters} className="px-4 py-2 rounded-lg bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium transition-colors">
                Apply Filters
              </button>
              <button
                onClick={() => { setFilters(EMPTY_FILTERS); setAppliedFilters(null); }}
                className="px-4 py-2 rounded-lg border border-dark-border text-gray-400 hover:text-white text-sm transition-colors"
              >
                Clear
              </button>
              {userId && hasAnyFilter && (
                showSaveBox ? (
                  <div className="flex items-center gap-2">
                    <input
                      type="text" value={saveName} onChange={(e) => setSaveName(e.target.value)}
                      placeholder="Name this screen…" maxLength={60} autoFocus
                      className="bg-dark-bg border border-dark-border rounded-lg px-2.5 py-1.5 text-sm text-white outline-none focus:border-brand-500"
                    />
                    <button
                      onClick={() => saveName.trim() && saveMutation.mutate(saveName.trim())}
                      disabled={!saveName.trim() || saveMutation.isPending}
                      className="px-3 py-1.5 rounded-lg bg-brand-500/20 text-brand-400 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium disabled:opacity-50"
                    >
                      {saveMutation.isPending ? "Saving…" : "Save"}
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowSaveBox(true)}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-dark-border text-gray-400 hover:text-white text-sm transition-colors"
                  >
                    <Save size={13} /> Save this search
                  </button>
                )
              )}
              {!userId && hasAnyFilter && (
                <span className="text-[11px] text-gray-500">Sign in to save this search for later.</span>
              )}
            </div>
          </div>
        </>
      )}

      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-x-auto">
        <table className="w-full text-sm min-w-[320px]">
          <thead>
            <tr className="border-b border-dark-border text-gray-400 text-left">
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium">Symbol</th>
              {mode === "filter" && <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium">Sector</th>}
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">{mode === "movers" ? "Price" : "P/E"}</th>
              {mode === "filter" && <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">ROE %</th>}
              {mode === "filter" && <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">Quality</th>}
              <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">{mode === "movers" ? "Change" : "Action"}</th>
              {mode === "movers" && <th className="px-3 sm:px-6 py-3 sm:py-4 font-medium text-right">Action</th>}
            </tr>
          </thead>
          <tbody>
            {mode === "movers" ? (
              isLoading
                ? Array.from({ length: 10 }).map((_, i) => (
                    <tr key={i} className="border-b border-dark-border">
                      <td colSpan={4} className="px-3 sm:px-6 py-3 sm:py-4">
                        <div className="h-4 bg-dark-border rounded animate-pulse" />
                      </td>
                    </tr>
                  ))
                : isError
                ? (
                    <tr><td colSpan={4} className="px-6 py-12 text-center text-gray-500 text-sm">Server starting up · Retrying automatically…</td></tr>
                  )
                : (!data?.movers?.length)
                ? (
                    <tr><td colSpan={4} className="px-6 py-12 text-center text-gray-500 text-sm">Market is closed — last session data will appear shortly.</td></tr>
                  )
                : data?.movers.map((stock) => (
                    <StockContextMenu key={stock.symbol} symbol={stock.symbol} market={market}>
                      <tr className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                        <td className="px-3 sm:px-6 py-3 sm:py-4 font-mono font-bold text-white">{stock.symbol}</td>
                        <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">{currency}{stock.price?.toLocaleString() ?? "—"}</td>
                        <td className={clsx("px-3 sm:px-6 py-3 sm:py-4 text-right font-medium", (stock.change_pct ?? 0) >= 0 ? "text-bull" : "text-bear")}>
                          <span className="flex items-center justify-end gap-1">
                            {(stock.change_pct ?? 0) >= 0 ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                            {(stock.change_pct ?? 0) >= 0 ? "+" : ""}{stock.change_pct ?? 0}%
                          </span>
                        </td>
                        <td className="px-3 sm:px-6 py-3 sm:py-4 text-right">
                          <Link href={`/stock/${stock.symbol}?market=${market}`} className="px-3 py-1 rounded-lg bg-brand-500/20 text-brand-500 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium transition-colors">
                            Analyse →
                          </Link>
                        </td>
                      </tr>
                    </StockContextMenu>
                  ))
            ) : appliedFilters === null ? (
              <tr><td colSpan={5} className="px-6 py-12 text-center text-gray-500 text-sm">Set your criteria above and click Apply Filters.</td></tr>
            ) : filterQuery.isLoading ? (
              Array.from({ length: 8 }).map((_, i) => (
                <tr key={i} className="border-b border-dark-border">
                  <td colSpan={5} className="px-3 sm:px-6 py-3 sm:py-4"><div className="h-4 bg-dark-border rounded animate-pulse" /></td>
                </tr>
              ))
            ) : filterQuery.data?.error ? (
              <tr><td colSpan={5} className="px-6 py-12 text-center text-gray-500 text-sm">{filterQuery.data.error}</td></tr>
            ) : !filterQuery.data?.results?.length ? (
              <tr><td colSpan={5} className="px-6 py-12 text-center text-gray-500 text-sm">No stocks match these criteria — try loosening a filter.</td></tr>
            ) : (
              filterQuery.data.results.map((stock) => (
                <StockContextMenu key={stock.symbol} symbol={stock.symbol} market={market}>
                  <tr className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                    <td className="px-3 sm:px-6 py-3 sm:py-4 font-mono font-bold text-white">{stock.symbol}</td>
                    <td className="px-3 sm:px-6 py-3 sm:py-4 text-gray-400 truncate max-w-[160px]">{stock.sector_name ?? "—"}</td>
                    <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">{stock.pe_ratio?.toFixed(1) ?? "—"}</td>
                    <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">{stock.roe_pct != null ? `${stock.roe_pct.toFixed(1)}%` : "—"}</td>
                    <td className="px-3 sm:px-6 py-3 sm:py-4 text-right font-mono">
                      {stock.business_quality_score != null ? Math.round(stock.business_quality_score) : "—"}
                      {stock.business_quality_grade && <span className="text-gray-500 ml-1">({stock.business_quality_grade})</span>}
                    </td>
                    <td className="px-3 sm:px-6 py-3 sm:py-4 text-right">
                      <Link href={`/stock/${stock.symbol}?market=${market}`} className="px-3 py-1 rounded-lg bg-brand-500/20 text-brand-500 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium transition-colors">
                        Analyse →
                      </Link>
                    </td>
                  </tr>
                </StockContextMenu>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
