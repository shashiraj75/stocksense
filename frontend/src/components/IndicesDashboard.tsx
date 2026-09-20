"use client";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ArrowDownRight, Minus, RefreshCw, Search, LayoutGrid } from "lucide-react";
import clsx from "clsx";
import { fetchIndices, type IndexQuote } from "@/utils/api";
import { getMarketStatus } from "@/utils/marketHours";

type Group = "IN" | "US" | "CRYPTO" | "COMMODITIES";

const GROUPS: { key: Group; label: string; flag: string }[] = [
  { key: "IN",          label: "NSE India",      flag: "🇮🇳" },
  { key: "US",          label: "NYSE / NASDAQ",  flag: "🇺🇸" },
  { key: "CRYPTO",      label: "Crypto",         flag: "₿" },
  { key: "COMMODITIES", label: "Commodities",    flag: "🪙" },
];

// 15s matches TickerRibbon's cadence — indices move fast enough that this
// reads as "real-time" without hammering the backend's own cached quote source.
const REFRESH_MS = 15_000;

function useGroupIndices(group: Group) {
  return useQuery({
    queryKey: ["indices-dashboard", group],
    queryFn: () => fetchIndices(group),
    staleTime: 10_000,
    refetchInterval: REFRESH_MS,
    retry: 1,
  });
}

function fmtNumber(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 });
}

function IndexCard({ idx }: { idx: IndexQuote }) {
  const hasData = idx.price != null;
  const pct = idx.change_pct ?? null;
  const up = pct !== null && pct > 0;
  const down = pct !== null && pct < 0;

  return (
    <div
      className={clsx(
        "group relative overflow-hidden rounded-2xl border bg-dark-card p-4 shadow-sm shadow-black/20",
        "transition-all duration-150 hover:-translate-y-0.5 hover:shadow-md hover:shadow-black/30",
        up && "border-bull/20 hover:border-bull/40",
        down && "border-bear/20 hover:border-bear/40",
        !up && !down && "border-dark-border hover:border-brand-500/30"
      )}
    >
      {/* Directional glow accent — purely decorative, communicates state at a glance */}
      <div
        className={clsx(
          "pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full blur-2xl opacity-30 transition-opacity duration-150 group-hover:opacity-50",
          up && "bg-bull",
          down && "bg-bear",
          !up && !down && "bg-brand-500"
        )}
      />

      <div className="relative flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">{idx.name}</p>
          <p className="font-mono text-[11px] text-gray-500">{idx.symbol}</p>
        </div>
        <span
          className={clsx(
            "flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
            up && "bg-bull/10 text-bull",
            down && "bg-bear/10 text-bear",
            !up && !down && "bg-white/5 text-gray-400"
          )}
        >
          {up ? <ArrowUpRight size={14} /> : down ? <ArrowDownRight size={14} /> : <Minus size={14} />}
        </span>
      </div>

      <div className="relative mt-3">
        {hasData ? (
          <p className="font-mono text-2xl font-bold tabular-nums text-white">{fmtNumber(idx.price!)}</p>
        ) : (
          <div className="h-8 w-24 animate-pulse rounded-lg bg-white/[0.04]" />
        )}

        <div className="mt-1 flex items-center gap-2">
          {pct !== null ? (
            <span
              className={clsx(
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold",
                up && "bg-bull/10 text-bull",
                down && "bg-bear/10 text-bear",
                !up && !down && "bg-white/5 text-gray-400"
              )}
            >
              {up ? "+" : ""}
              {pct.toFixed(2)}%
            </span>
          ) : (
            <div className="h-5 w-14 animate-pulse rounded-full bg-white/[0.04]" />
          )}
          {idx.change_pts !== null && idx.change_pts !== undefined && (
            <span className={clsx("font-mono text-xs tabular-nums", up ? "text-bull" : down ? "text-bear" : "text-gray-500")}>
              {up ? "+" : ""}
              {fmtNumber(idx.change_pts)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function IndexCardSkeleton() {
  return (
    <div className="rounded-2xl border border-dark-border bg-dark-card p-4 shadow-sm shadow-black/20">
      <div className="flex items-start justify-between gap-2">
        <div className="w-2/3 space-y-1.5">
          <div className="h-3.5 w-full animate-pulse rounded bg-white/[0.05]" />
          <div className="h-2.5 w-1/2 animate-pulse rounded bg-white/[0.04]" />
        </div>
        <div className="h-7 w-7 shrink-0 animate-pulse rounded-full bg-white/[0.04]" />
      </div>
      <div className="mt-3 space-y-2">
        <div className="h-7 w-28 animate-pulse rounded-lg bg-white/[0.05]" />
        <div className="h-5 w-16 animate-pulse rounded-full bg-white/[0.04]" />
      </div>
    </div>
  );
}

/** Self-contained, high-fidelity dashboard for tracking real-time index
 * prices across markets. Groups are fetched independently (only the active
 * tab is on a live poll) and reuse the same `/api/stocks/indices` endpoint
 * and cadence as TickerRibbon, so switching tabs here never triggers a
 * fresh scrape — just a cache hit or a short wait for the next tick. */
export function IndicesDashboard() {
  const [group, setGroup] = useState<Group>("IN");
  const [query, setQuery] = useState("");

  const { data, isLoading, isFetching, isError, dataUpdatedAt } = useGroupIndices(group);
  const marketStatus = getMarketStatus(group === "COMMODITIES" ? "US" : group);

  const indices = useMemo(() => {
    const all = data?.indices ?? [];
    if (!query.trim()) return all;
    const q = query.trim().toLowerCase();
    return all.filter((i) => i.name.toLowerCase().includes(q) || i.symbol.toLowerCase().includes(q));
  }, [data, query]);

  const lastUpdatedLabel = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <div className="w-full rounded-2xl border border-dark-border bg-dark-bg/40 p-4 sm:p-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-500/10 text-brand-400">
            <LayoutGrid size={18} />
          </span>
          <div>
            <h2 className="text-base font-bold text-white sm:text-lg">Market Indices</h2>
            <p className="text-xs text-gray-500">Live index levels across covered markets</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="relative flex h-2 w-2 shrink-0">
            {marketStatus.isOpen && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75" />
            )}
            <span className={clsx("relative inline-flex h-2 w-2 rounded-full", marketStatus.isOpen ? "bg-green-500" : "bg-red-500")} />
          </span>
          <span className={clsx("text-xs font-semibold", marketStatus.isOpen ? "text-green-400" : "text-red-400")}>
            {marketStatus.isOpen ? "Live" : "Closed"}
          </span>
          <span className="hidden items-center gap-1 text-xs text-gray-500 sm:inline-flex">
            <RefreshCw size={12} className={clsx(isFetching && "animate-spin")} />
            {lastUpdatedLabel ? `Updated ${lastUpdatedLabel}` : "Updating…"}
          </span>
        </div>
      </div>

      {/* Controls: group tabs + search */}
      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div
          role="tablist"
          aria-label="Market group"
          className="flex w-full gap-1.5 overflow-x-auto rounded-xl border border-dark-border bg-dark-card p-1 sm:w-auto"
        >
          {GROUPS.map((g) => (
            <button
              key={g.key}
              role="tab"
              aria-selected={group === g.key}
              onClick={() => setGroup(g.key)}
              className={clsx(
                "shrink-0 rounded-lg px-3 py-1.5 text-xs font-semibold transition-all duration-150",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400",
                group === g.key
                  ? "bg-brand-500 text-white shadow-sm shadow-brand-500/30"
                  : "text-gray-400 hover:bg-white/[0.04] hover:text-gray-200"
              )}
            >
              <span className="mr-1">{g.flag}</span>
              {g.label}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-56">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter indices…"
            className="w-full rounded-lg border border-dark-border bg-dark-card py-1.5 pl-8 pr-3 text-xs text-gray-200 placeholder:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
          />
        </div>
      </div>

      {/* Grid */}
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {isLoading
          ? Array.from({ length: 8 }).map((_, i) => <IndexCardSkeleton key={i} />)
          : isError
          ? (
            <div className="col-span-full flex flex-col items-center justify-center gap-2 rounded-xl border border-dark-border bg-dark-card py-10 text-center">
              <p className="text-sm font-medium text-gray-300">Index data temporarily unavailable</p>
              <p className="text-xs text-gray-500">Check back shortly — this refreshes automatically.</p>
            </div>
          )
          : indices.length === 0
          ? (
            <div className="col-span-full flex flex-col items-center justify-center gap-1 rounded-xl border border-dark-border bg-dark-card py-10 text-center">
              <p className="text-sm font-medium text-gray-300">No indices match &ldquo;{query}&rdquo;</p>
            </div>
          )
          : indices.map((idx) => <IndexCard key={idx.symbol} idx={idx} />)}
      </div>

      <p className="mt-4 text-center text-[11px] text-gray-600 sm:text-left">
        Index levels are for informational tracking only — not investment advice.
      </p>
    </div>
  );
}
