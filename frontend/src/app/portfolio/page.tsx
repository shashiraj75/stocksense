"use client";
import { useState, useEffect, useMemo, Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchQuote, fetchSignalSummary, fetchSectorsBatch, Market, api } from "@/utils/api";
import { useStaggeredQueries } from "@/hooks/useStaggeredQueries";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { SignalBadge } from "@/components/SignalBadge";
import Link from "next/link";
import clsx from "clsx";
import { PlusCircle, Trash2, TrendingUp, TrendingDown, Briefcase, Wifi, Pencil, Check, X, Upload, Download, ArrowUp, ArrowDown, ArrowUpDown } from "lucide-react";
import { PortfolioAllocationChart } from "@/components/PortfolioAllocationChart";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { StockSymbolField } from "@/components/StockSymbolField";
import type { StockResult } from "@/hooks/useStockSearch";
import { useAuth } from "@/lib/AuthContext";
import { ImportPortfolioModal } from "@/components/ImportPortfolioModal";
import { exportPortfolioToExcel } from "@/utils/portfolioExport";
import { UnsupportedMarketNotice } from "@/components/UnsupportedMarketNotice";

export interface Holding {
  id: string;
  symbol: string;
  market: Market;
  qty: number;
  avgPrice: number;
}

// localStorage is now just a fast-access cache / offline fallback — the
// backend (Postgres, via /api/portfolio) is the source of truth so holdings
// sync across devices for the same logged-in user instead of being stuck on
// whichever browser they were added from.
const STORAGE_KEY = "stocksense_portfolio";

export type Row = Holding & {
  curPrice: number | null;
  invested: number;
  current: number | null;
  plAmt: number | null;
  plPct: number | null;
  // Today's move only — independent of P&L (which is since avg buy price).
  // dayChangeAmt is this position's dollar/rupee move today (qty * the
  // quote's own per-share `change`); dayChangePct is the stock's own day
  // % change, reused as-is since %-change of a position's value is
  // identical to %-change of its price regardless of quantity held.
  dayChangeAmt: number | null;
  dayChangePct: number | null;
  loading: boolean;
  signal: string | null;
  confidence?: number;
  sigLoading: boolean;
  // Sourced from a single lightweight batch lookup against the
  // nightly-refreshed stock_fundamentals_cache table (fetchSectorsBatch),
  // one request per market for the whole holdings list — deliberately NOT
  // the per-holding AI signal/prediction pipeline. Sector allocation used
  // to be gated on every holding's full signal resolving (staggered, slow
  // on a cold cache), so the allocation chart could sit on a misleading
  // "Loading… 100%" bar for as long as that took, even though this data
  // was sitting in a cache table the whole time. Null/undefined until the
  // batch resolves or when the cache genuinely has no sector for it.
  sector?: string | null;
  // True only while THIS holding's market-wide sector batch request is in
  // flight (one request total per market, not per holding) — distinct from
  // sigLoading, which still gates the Signal badge only.
  sectorLoading: boolean;
};

function HoldingRow({
  r, currency, onRemove, onEdit,
}: { r: Row; currency: string; onRemove: (id: string) => void; onEdit: (id: string, updates: { qty: number; avgPrice: number }) => void }) {
  const [editing, setEditing] = useState(false);
  const [qtyInput, setQtyInput] = useState(String(r.qty));
  const [avgInput, setAvgInput] = useState(String(r.avgPrice));

  const startEdit = () => {
    setQtyInput(String(r.qty));
    setAvgInput(String(r.avgPrice));
    setEditing(true);
  };

  const confirm = () => {
    const q = parseFloat(qtyInput);
    const a = parseFloat(avgInput);
    if (!q || q <= 0 || !a || a <= 0) return; // ignore invalid input, keep editing open
    onEdit(r.id, { qty: q, avgPrice: a });
    setEditing(false);
  };

  return (
    <tr className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
      <td className="px-4 py-3">
        <Link href={`/stock/${r.symbol}?market=${r.market}`}
          className="font-mono font-bold text-white hover:text-brand-500 transition-colors">
          {r.symbol}
        </Link>
      </td>
      <td className="px-4 py-3 text-right font-mono">
        {editing ? (
          <input type="number" min="0" step="1" value={qtyInput} onChange={e => setQtyInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") confirm(); if (e.key === "Escape") setEditing(false); }}
            className="w-20 bg-dark-bg border border-brand-500/60 rounded-lg px-2 py-1 text-right text-xs font-mono text-white focus:outline-none" />
        ) : r.qty}
      </td>
      <td className="px-4 py-3 text-right font-mono">
        {editing ? (
          <input type="number" min="0" step="0.01" value={avgInput} onChange={e => setAvgInput(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") confirm(); if (e.key === "Escape") setEditing(false); }}
            className="w-24 bg-dark-bg border border-brand-500/60 rounded-lg px-2 py-1 text-right text-xs font-mono text-white focus:outline-none" />
        ) : `${currency}${r.avgPrice.toLocaleString()}`}
      </td>
      <td className="px-4 py-3 text-right font-mono">
        {r.loading ? <span className="animate-pulse text-gray-500">…</span>
          : r.curPrice ? `${currency}${r.curPrice.toLocaleString()}` : "—"}
      </td>
      <td className="px-4 py-3 text-right font-mono text-gray-300">{currency}{r.invested.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td className="px-4 py-3 text-right font-mono">
        {r.current !== null ? `${currency}${r.current.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
      </td>
      <td className={clsx("px-4 py-3 text-right font-mono font-bold whitespace-nowrap",
        r.dayChangeAmt === null ? "text-gray-500" : r.dayChangeAmt >= 0 ? "text-bull" : "text-bear")}>
        {r.dayChangeAmt !== null ? `${r.dayChangeAmt >= 0 ? "+" : ""}${currency}${Math.abs(r.dayChangeAmt).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
      </td>
      <td className={clsx("px-4 py-3 text-right font-mono font-bold whitespace-nowrap",
        r.dayChangePct === null ? "text-gray-500" : r.dayChangePct >= 0 ? "text-bull" : "text-bear")}>
        {r.dayChangePct !== null
          ? <span className="flex items-center justify-end gap-1">
              {r.dayChangePct >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
              {r.dayChangePct >= 0 ? "+" : ""}{r.dayChangePct.toFixed(1)}%
            </span>
          : "—"}
      </td>
      <td className={clsx("px-4 py-3 text-right font-mono font-bold whitespace-nowrap",
        r.plAmt === null ? "text-gray-500" : r.plAmt >= 0 ? "text-bull" : "text-bear")}>
        {r.plAmt !== null ? `${r.plAmt >= 0 ? "+" : ""}${currency}${Math.abs(r.plAmt).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
      </td>
      <td className={clsx("px-4 py-3 text-right font-mono font-bold whitespace-nowrap",
        r.plPct === null ? "text-gray-500" : r.plPct >= 0 ? "text-bull" : "text-bear")}>
        {r.plPct !== null
          ? <span className="flex items-center justify-end gap-1">
              {r.plPct >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
              {r.plPct >= 0 ? "+" : ""}{r.plPct.toFixed(1)}%
            </span>
          : "—"}
      </td>
      <td className="px-4 py-3 text-center">
        {r.sigLoading ? (
          <span
            className="inline-flex items-center gap-1 text-gray-500 text-xs animate-pulse"
            title="Computing this stock's signal — large portfolios compute a few holdings at a time, so this can take a while to reach every row."
          >
            <span className="w-1.5 h-1.5 rounded-full bg-gray-500" />
            computing
          </span>
        ) : r.signal ? (
          <SignalBadge signal={r.signal as any} confidence={r.confidence} size="sm" />
        ) : (
          <span className="text-gray-600 text-xs">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        {editing ? (
          <div className="flex items-center justify-end gap-1">
            <button onClick={confirm} className="p-1 rounded text-bull hover:bg-bull/10 transition-colors"><Check size={14} /></button>
            <button onClick={() => setEditing(false)} className="p-1 rounded text-gray-400 hover:bg-white/10 transition-colors"><X size={14} /></button>
          </div>
        ) : (
          <div className="flex items-center justify-end gap-1">
            <button onClick={startEdit} title="Edit qty / avg buy price" className="p-1 rounded text-gray-500 hover:text-white transition-colors"><Pencil size={13} /></button>
            <button onClick={() => onRemove(r.id)} className="p-1 rounded text-gray-500 hover:text-bear transition-colors"><Trash2 size={14} /></button>
          </div>
        )}
      </td>
    </tr>
  );
}

type SortKey = "symbol" | "qty" | "avgPrice" | "curPrice" | "invested" | "current" | "dayChangeAmt" | "dayChangePct" | "plAmt" | "plPct" | "signal";

const SORT_ACCESSORS: Record<SortKey, (r: Row) => string | number | null> = {
  symbol: (r) => r.symbol,
  qty: (r) => r.qty,
  avgPrice: (r) => r.avgPrice,
  curPrice: (r) => r.curPrice,
  invested: (r) => r.invested,
  current: (r) => r.current,
  dayChangeAmt: (r) => r.dayChangeAmt,
  dayChangePct: (r) => r.dayChangePct,
  plAmt: (r) => r.plAmt,
  plPct: (r) => r.plPct,
  signal: (r) => r.signal,
};

function SortableHeader({
  label, sortKey, align, activeKey, dir, onSort, title,
}: { label: string; sortKey: SortKey; align?: "right" | "center"; activeKey: SortKey | null; dir: "asc" | "desc"; onSort: (key: SortKey) => void; title?: string }) {
  const isActive = activeKey === sortKey;
  return (
    <th title={title} className={clsx("px-4 py-3 font-medium select-none whitespace-nowrap", align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left")}>
      <button
        onClick={() => onSort(sortKey)}
        className={clsx(
          "flex items-center gap-1 hover:text-white transition-colors whitespace-nowrap",
          align === "right" ? "ml-auto" : align === "center" ? "mx-auto" : "",
          isActive ? "text-white" : "text-gray-400"
        )}
      >
        {label}
        {isActive ? (dir === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ArrowUpDown size={12} className="opacity-40" />}
      </button>
    </th>
  );
}

// Exported purely for direct unit testing of sector grouping (percentage
// headings, unresolved-vs-Other separation, market scoping) without
// needing to render the full data-fetching PortfolioPage — no behavior
// change, this component doesn't fetch anything itself.
export function HoldingsTable({
  rows, currency, onRemove, onEdit, groupBySector,
}: {
  rows: Row[];
  currency: string;
  onRemove: (id: string) => void;
  onEdit: (id: string, updates: { qty: number; avgPrice: number }) => void;
  // Driven by the Portfolio Allocation chart's "By Sector/By Stock" toggle
  // (lifted to the parent page) instead of a separate button on this table —
  // one control, two views in sync.
  groupBySector: boolean;
}) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  // Sprint 011 (§20.1 memoization): re-sort only when the rows or the sort
  // state change, not on every parent render (audit finding 3(b)).
  const sortedRows = useMemo(() => sortKey ? [...rows].sort((a, b) => {
    const av = SORT_ACCESSORS[sortKey](a);
    const bv = SORT_ACCESSORS[sortKey](b);
    // Nulls (still loading / no data) always sink to the bottom regardless
    // of sort direction — otherwise toggling to descending would put
    // "still loading" rows at the top, which looks broken.
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === "string" ? av.localeCompare(bv as string) : av - (bv as number);
    return sortDir === "asc" ? cmp : -cmp;
  }) : rows, [rows, sortKey, sortDir]);

  // Buckets the already-sorted rows by sector (same "Other" fallback the
  // allocation chart's sectorSlices already uses, so the two views agree),
  // ordered by each group's own current value descending — largest sector
  // first, matching how the allocation chart itself orders sectors.
  //
  // Rows whose sector lookup hasn't resolved yet get their own "Resolving
  // sector…" bucket rather than falling into "Other" — otherwise a
  // portfolio can look like every holding is unclassified for as long as
  // that lookup takes, when really most of them just haven't answered yet.
  // Always sorted last regardless of size — it's a transient state, not a
  // real category — and its value is never folded into "Other" or into the
  // percentage-of-portfolio denominator used for the resolved groups below.
  const marketTotalValue = useMemo(
    () => sortedRows.reduce((s, r) => s + (r.current ?? 0), 0),
    [sortedRows]
  );
  const sectorGroups = useMemo(() => {
    if (!groupBySector) return null;
    const byName = new Map<string, Row[]>();
    const unresolvedRows: Row[] = [];
    for (const r of sortedRows) {
      if (r.sectorLoading) { unresolvedRows.push(r); continue; }
      const name = r.sector?.trim() || "Other";
      if (!byName.has(name)) byName.set(name, []);
      byName.get(name)!.push(r);
    }
    const groups: { sector: string; rows: Row[]; totalValue: number; resolved: boolean }[] =
      Array.from(byName, ([sector, groupRows]) => ({
        sector,
        rows: groupRows,
        totalValue: groupRows.reduce((s, r) => s + (r.current ?? 0), 0),
        resolved: true,
      })).sort((a, b) => b.totalValue - a.totalValue);
    if (unresolvedRows.length > 0) {
      groups.push({
        sector: "Resolving sector…",
        rows: unresolvedRows,
        totalValue: unresolvedRows.reduce((s, r) => s + (r.current ?? 0), 0),
        resolved: false,
      });
    }
    return groups;
  }, [sortedRows, groupBySector]);

  // 11 header cells (SortableHeader) + 1 trailing actions <th> = 12 columns.
  const COLUMN_COUNT = 12;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
      {/* A wide table (11 columns) needs horizontal scroll on anything
          narrower than a large desktop. Native scrollbars are invisible
          until actively scrolling on macOS/trackpad systems, which makes a
          cut-off table look broken rather than "scroll for more" — force a
          persistently visible, styled thin scrollbar instead of relying on
          the OS default. */}
      <div className="overflow-x-auto [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-dark-border [&::-webkit-scrollbar-thumb]:rounded-full">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-dark-border text-gray-400 text-left">
              <SortableHeader label="Symbol" sortKey="symbol" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Qty" sortKey="qty" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Avg Buy" sortKey="avgPrice" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Current" sortKey="curPrice" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Invested" sortKey="invested" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Value" sortKey="current" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Day's P&L" sortKey="dayChangeAmt" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort}
                title="How much this position moved today — independent of your overall P&L since purchase." />
              <SortableHeader label="Day's P&L %" sortKey="dayChangePct" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort}
                title="How much this position moved today — independent of your overall P&L since purchase." />
              <SortableHeader label="P&L" sortKey="plAmt" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="P&L %" sortKey="plPct" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Signal" sortKey="signal" align="center" activeKey={sortKey} dir={sortDir} onSort={handleSort}
                title="Today's forward-looking AI call for this stock — independent of your P&L. A BUY here doesn't retroactively justify your original entry price, and a HOLD/SELL doesn't mean you're wrong to be holding; it reflects current conditions, not your specific cost basis." />
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {sectorGroups ? (
              sectorGroups.map((g) => {
                // Percentage of the selected market's total portfolio value
                // — only meaningful for a resolved sector (the denominator
                // is the whole market, not just what's resolved so far, so
                // this also communicates "how much of my portfolio is this,"
                // not just "how much of what's been classified"). Omitted
                // for the unresolved bucket — a %-of-portfolio figure next
                // to "Resolving…" would read as a real allocation number.
                const pct = g.resolved && marketTotalValue > 0 ? (g.totalValue / marketTotalValue) * 100 : null;
                return (
                  <Fragment key={g.sector}>
                    <tr className={clsx("bg-dark-bg/60", !g.resolved && "opacity-70")}>
                      <td colSpan={COLUMN_COUNT} className="px-4 py-2 text-xs font-semibold text-gray-300">
                        {!g.resolved && <span className="inline-block w-1.5 h-1.5 rounded-full bg-gray-500 animate-pulse mr-1.5" />}
                        {g.sector}
                        <span className="ml-2 font-normal text-gray-500">
                          {g.rows.length} holding{g.rows.length !== 1 ? "s" : ""} · {currency}{g.totalValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                          {pct !== null && ` · ${pct.toFixed(1)}%`}
                        </span>
                      </td>
                    </tr>
                    {g.rows.map((r) => (
                      <HoldingRow key={r.id} r={r} currency={currency} onRemove={onRemove} onEdit={onEdit} />
                    ))}
                  </Fragment>
                );
              })
            ) : (
              sortedRows.map((r) => (
                <HoldingRow key={r.id} r={r} currency={currency} onRemove={onRemove} onEdit={onEdit} />
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function loadLocal(): Holding[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
}
function saveLocal(h: Holding[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(h));
}

export default function PortfolioPage() {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const apiBase = userId ? `/api/portfolio/${userId}` : null;

  // Starts `[]` unconditionally — NOT `() => loadLocal()` — because
  // localStorage doesn't exist during server-side rendering. The old
  // synchronous-read initializer made `holdings` (and therefore every
  // render branch that keys off `holdings.length`/`rows.length` — the
  // empty-state message, the Export button's disabled state, the summary
  // cards, the whole holdings-table section) disagree between server and
  // client on the very first paint for any returning user with stored
  // holdings, each a separate real hydration-mismatch error confirmed live
  // one at a time. Mirrors the existing, already-correct pattern in
  // useMarketPreference.ts (empty-safe initializer + client-only effect for
  // the real value) instead of adding a `mounted`-style guard at every one
  // of those call sites individually.
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [sym, setSym] = useState("");
  const [market, setMarket] = useMarketPreference(["IN", "US"] as const, "IN");
  const [qty, setQty] = useState("");
  const [avgPrice, setAvgPrice] = useState("");
  const [error, setError] = useState("");
  const [showImport, setShowImport] = useState(false);
  // Collapsed by default once holdings already exist — the form doesn't need
  // to stay permanently visible for a returning user, and its own real
  // estate was the thing this was collapsed to reduce. Defaults open for a
  // genuinely empty portfolio so the "add your first stock above" empty
  // state (below) still points at something visible.
  const [showAddHolding, setShowAddHolding] = useState(false);
  useEffect(() => {
    const loaded = loadLocal();
    setHoldings(loaded);
    if (loaded.length === 0) setShowAddHolding(true);
    // Intentionally once-on-mount only — loads the real (client-only)
    // localStorage value and sets the form's initial open/closed state from
    // it in the same pass, avoiding an effect-ordering dependency between
    // two separate effects that would otherwise both need to run after this
    // same client-only read. Must not re-open the form every time the last
    // holding gets deleted later, hence the empty deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Same hydration-mismatch class as showAddHolding above, found live while
  // verifying it: useAuth()'s user (and therefore apiBase, derived from it)
  // resolves asynchronously/client-only, so the Import Portfolio button's
  // `disabled={!apiBase}` rendered differently on the server (always
  // disabled — no session context during SSR) vs. the client's first paint
  // (already-resolved auth state). Gating on `mounted` forces both to agree
  // on "disabled" until the client-only effect below flips it, exactly
  // mirroring the showAddHolding fix.
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  // Load from backend when the user is ready. If the server has nothing yet
  // but this browser's localStorage does, migrate those old local-only
  // holdings up to the server once, so they don't silently disappear for
  // anyone who used Portfolio before it synced across devices.
  useEffect(() => {
    if (!apiBase) return;
    api.get<{ items: Holding[] }>(apiBase)
      .then(async res => {
        const serverItems = res.data.items;
        if (serverItems.length > 0) {
          setHoldings(serverItems);
          saveLocal(serverItems);
          return;
        }
        const local = loadLocal();
        if (local.length === 0) return;
        const migrated: Holding[] = [];
        for (const h of local) {
          try {
            const created = await api.post<Holding>(apiBase, { symbol: h.symbol, market: h.market, qty: h.qty, avg_price: h.avgPrice }).then(r => r.data);
            migrated.push(created);
          } catch { /* skip holdings that fail to migrate — better than losing the whole list */ }
        }
        setHoldings(migrated);
        saveLocal(migrated);
      })
      .catch(() => setHoldings(loadLocal()));
  }, [apiBase]);

  const refetchHoldings = () => {
    if (!apiBase) return;
    api.get<{ items: Holding[] }>(apiBase)
      .then(res => { setHoldings(res.data.items); saveLocal(res.data.items); })
      .catch(() => {});
  };

  // Staggered, not plain useQueries — firing one quote + one prediction
  // request per holding simultaneously hits the browser's per-origin
  // connection cap once a portfolio has more than a handful of rows,
  // leaving most of them stuck loading even though the backend itself
  // handles the concurrent load fine.
  const quoteQueries = useStaggeredQueries(
    holdings.map(h => ({
      queryKey: ["quote", h.symbol, h.market],
      queryFn: () => fetchQuote(h.symbol, h.market),
      staleTime: 5 * 60_000,
    })),
    8
  );

  // Sprint 011 (§20.1): the badge column only needs {signal, confidence},
  // so fetch the signal-only summary instead of the full multi-engine
  // prediction payload per holding. Same backend cache, same values, same
  // 202-then-poll contract — just a few-field response per row instead of
  // the whole engine dump.
  const signalQueries = useStaggeredQueries(
    holdings.map(h => ({
      queryKey: ["signal", h.symbol, h.market, "medium"],
      queryFn: () => fetchSignalSummary(h.symbol, h.market, "medium"),
      staleTime: 15 * 60_000,   // predictions cache for 15 min
      retry: 1,
    })),
    8 // load-tested: 8 concurrent fresh predictions resolve in ~12s with no
      // degradation vs 5 in ~9s — was 6 mainly out of caution before the
      // event-loop fixes; raised to cut the number of sequential batches a
      // large portfolio needs (e.g. 38 holdings: 7 batches -> 5).
  );

  // Sector allocation's data source - deliberately independent of
  // signalQueries above. One batch request per market for the WHOLE
  // holdings list (not staggered, not one-per-holding, not gated on any
  // AI prediction), sourced from the nightly-refreshed
  // stock_fundamentals_cache table. This is what lets the allocation
  // chart resolve real sectors immediately instead of waiting on however
  // long the staggered per-holding signal computation takes.
  const inSymbols = useMemo(() => holdings.filter(h => h.market === "IN").map(h => h.symbol), [holdings]);
  const usSymbols = useMemo(() => holdings.filter(h => h.market === "US").map(h => h.symbol), [holdings]);
  const inSectorsQuery = useQuery({
    queryKey: ["portfolio-sectors", "IN", inSymbols.join(",")],
    queryFn: () => fetchSectorsBatch(inSymbols, "IN"),
    enabled: inSymbols.length > 0,
    staleTime: 30 * 60_000, // a stock's sector classification doesn't change day to day
  });
  const usSectorsQuery = useQuery({
    queryKey: ["portfolio-sectors", "US", usSymbols.join(",")],
    queryFn: () => fetchSectorsBatch(usSymbols, "US"),
    enabled: usSymbols.length > 0,
    staleTime: 30 * 60_000,
  });

  const add = async () => {
    setError("");
    if (!sym.trim()) return setError("Enter a symbol");
    if (!qty || isNaN(+qty) || +qty <= 0) return setError("Enter valid quantity");
    if (!avgPrice || isNaN(+avgPrice) || +avgPrice <= 0) return setError("Enter valid buy price");

    const payload = { symbol: sym.trim().toUpperCase(), market, qty: +qty, avgPrice: +avgPrice };
    let newHolding: Holding;
    try {
      if (!apiBase) throw new Error("Not logged in");
      newHolding = await api.post<Holding>(apiBase, { ...payload, avg_price: payload.avgPrice }).then(r => r.data);
    } catch {
      newHolding = { id: Date.now().toString(), ...payload }; // offline fallback — local-only
    }
    const updated = [...holdings, newHolding];
    setHoldings(updated); saveLocal(updated);
    setSym(""); setQty(""); setAvgPrice("");
  };

  // Await the backend call before touching local state — firing the request
  // and updating state unconditionally would let a failed delete/edit leave
  // the row alive server-side while the UI shows it gone/changed, and the
  // next load's GET would silently revert it (same bug class fixed in
  // Alerts earlier).
  const remove = async (id: string) => {
    if (apiBase) {
      try { await api.delete(`${apiBase}/${id}`); }
      catch { setError("Couldn't delete that holding — check your connection and try again."); return; }
    }
    const updated = holdings.filter(h => h.id !== id);
    setHoldings(updated); saveLocal(updated);
  };

  const edit = async (id: string, updates: { qty: number; avgPrice: number }) => {
    if (apiBase) {
      try { await api.patch(`${apiBase}/${id}`, { qty: updates.qty, avg_price: updates.avgPrice }); }
      catch { setError("Couldn't update that holding — check your connection and try again."); return; }
    }
    const updated = holdings.map(h => h.id === id ? { ...h, ...updates } : h);
    setHoldings(updated); saveLocal(updated);
  };

  const currency = (m: Market) => m === "US" ? "$" : "₹";

  // Sprint 011 (§20.1 memoization): the per-row P&L/allocation math used to
  // re-run on every render — including every keystroke in the Add Holding
  // form, whose inputs are sibling useState in this same component (audit
  // finding 3(b)). The query-result arrays get a new identity every render,
  // so the memo keys on a compact content signature of the data the rows
  // actually consume instead of on the arrays themselves.
  const quoteSig = quoteQueries.map(q => `${q.isLoading ? "L" : ""}${q.data?.price ?? ""}`).join("|");
  const signalSig = signalQueries.map(q => `${q.isLoading ? "L" : ""}${q.data?.signal ?? ""}:${q.data?.confidence ?? ""}`).join("|");
  const sectorsSig = `${inSectorsQuery.isLoading ? "L" : ""}${JSON.stringify(inSectorsQuery.data ?? {})}|${usSectorsQuery.isLoading ? "L" : ""}${JSON.stringify(usSectorsQuery.data ?? {})}`;

  const { rows, totalInvestedIN, totalCurrentIN, totalInvestedUS, totalCurrentUS, totalDayChangeIN, totalDayChangeUS } = useMemo(() => {
    // Compute totals per currency — never mix ₹ and $ into one number
    let totalInvestedIN = 0, totalCurrentIN = 0;
    let totalInvestedUS = 0, totalCurrentUS = 0;
    let totalDayChangeIN = 0, totalDayChangeUS = 0;

    const rows = holdings.map((h, i) => {
      const q = quoteQueries[i]?.data;
      const curPrice = q?.price ?? null;
      const invested = h.qty * h.avgPrice;
      const current = curPrice ? h.qty * curPrice : null;
      const plAmt = current !== null ? current - invested : null;
      const plPct = plAmt !== null ? (plAmt / invested) * 100 : null;
      // Today's move — the quote's own per-share change/change_pct, never
      // derived from avgPrice (that would conflate "today" with "since
      // purchase", exactly the confusion the Signal column's own tooltip
      // already warns against for a different field).
      const dayChangeAmt = q && q.change != null ? h.qty * q.change : null;
      const dayChangePct = q?.change_pct ?? null;
      if (current !== null) {
        if (h.market === "IN") { totalInvestedIN += invested; totalCurrentIN += current; }
        else { totalInvestedUS += invested; totalCurrentUS += current; }
      }
      if (dayChangeAmt !== null) {
        if (h.market === "IN") totalDayChangeIN += dayChangeAmt;
        else totalDayChangeUS += dayChangeAmt;
      }
      const sig = signalQueries[i]?.data;
      const signal = sig?.signal ?? null;
      const confidence = sig?.confidence ?? undefined;
      // Sourced from the lightweight batch lookup, keyed per-market — never
      // from the signal query's own quality_factors.sector anymore, so
      // sector allocation no longer waits on a per-holding AI computation.
      const sectorsQuery = h.market === "IN" ? inSectorsQuery : usSectorsQuery;
      const sector = sectorsQuery.data?.[h.symbol.toUpperCase()] ?? null;
      const sectorLoading = sectorsQuery.isLoading;
      return { ...h, curPrice, invested, current, plAmt, plPct, dayChangeAmt, dayChangePct, loading: quoteQueries[i]?.isLoading, signal, confidence, sigLoading: signalQueries[i]?.isLoading, sector, sectorLoading };
    });
    return { rows, totalInvestedIN, totalCurrentIN, totalInvestedUS, totalCurrentUS, totalDayChangeIN, totalDayChangeUS };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdings, quoteSig, signalSig, sectorsSig]);

  // Sprint 011 (§20.1 memoization): the per-market row splits and the
  // allocation-chart slices are derived views of the memoized rows — keep
  // their identities stable too so they don't churn on unrelated renders.
  const inRows = useMemo(() => rows.filter(r => r.market === "IN"), [rows]);
  const usRows = useMemo(() => rows.filter(r => r.market === "US"), [rows]);
  const chartSlices = useMemo(() =>
    rows.filter(r => r.market === market).map(r => ({
      symbol: r.symbol,
      value: r.current ?? 0,
      signal: r.signal,
    })).sort((a, b) => b.value - a.value), [rows, market]);

  // Sector-wise grouping for the allocation chart's default view — sums
  // current value per REAL, resolved sector. Unresolved holdings are
  // tracked separately (unresolvedSectorValue/Count below), never folded
  // into this array as a fake "Loading…" sector slice — a slice in here is
  // always a genuine investment sector (or the resolved-but-unclassified
  // "Other" bucket), so the chart can never mistake "nothing has resolved
  // yet" for "the whole portfolio is one real category."
  const sectorSlices = useMemo(() => {
    const totals = new Map<string, number>();
    for (const r of rows) {
      if (r.market !== market || !r.current || r.sectorLoading) continue;
      const sector = r.sector?.trim() || "Other";
      totals.set(sector, (totals.get(sector) ?? 0) + r.current);
    }
    return Array.from(totals, ([sector, value]) => ({ sector, value }))
      .sort((a, b) => b.value - a.value);
  }, [rows, market]);

  // Value/count still resolving sector for the selected market — surfaced
  // to the allocation chart as a distinct "Resolving sectors…" state, never
  // merged into sectorSlices above and never counted as "Other" (which must
  // only ever mean "resolved, and there's genuinely no sector for it").
  const { unresolvedSectorValue, unresolvedSectorCount } = useMemo(() => {
    let value = 0, count = 0;
    for (const r of rows) {
      if (r.market !== market || !r.current || !r.sectorLoading) continue;
      value += r.current;
      count += 1;
    }
    return { unresolvedSectorValue: value, unresolvedSectorCount: count };
  }, [rows, market]);

  // Single shared toggle for both the allocation chart's grouping and the
  // holdings table's grouping — one control, two views in sync, instead of
  // a separate "Group by Sector" button duplicating the same choice. null =
  // "follow the data" (defaults to sector once sector data arrives, which
  // resolves asynchronously after mount) until the user explicitly clicks.
  const [allocationMode, setAllocationMode] = useState<"sector" | "stock" | null>(null);
  const hasSectorData = sectorSlices.some(s => s.value > 0);
  const effectiveAllocationMode = allocationMode ?? (hasSectorData ? "sector" : "stock");

  // Gated on the selected market toggle too, not just whether holdings exist —
  // otherwise both currencies' summary cards/tables/chart show simultaneously
  // regardless of which market is selected, unlike every other page's market
  // toggle (Daily Picks, Dashboard, Heatmap), which filters the whole view.
  const hasIN = market === "IN" && totalInvestedIN > 0;
  const hasUS = market === "US" && totalInvestedUS > 0;
  const hasINHoldings = market === "IN" && holdings.some(h => h.market === "IN");
  const hasUSHoldings = market === "US" && holdings.some(h => h.market === "US");
  const totalPLIN = totalCurrentIN - totalInvestedIN;
  const totalPLUS = totalCurrentUS - totalInvestedUS;
  const totalPLPctIN = totalInvestedIN > 0 ? (totalPLIN / totalInvestedIN) * 100 : 0;
  const totalPLPctUS = totalInvestedUS > 0 ? (totalPLUS / totalInvestedUS) * 100 : 0;
  // Portfolio-level day % change — weighted by value, not a simple average
  // of each stock's own %. Yesterday's total value = today's total value
  // minus today's total move; dividing by that (not by today's value)
  // gives the correct % change from yesterday's close to today.
  const prevValueIN = totalCurrentIN - totalDayChangeIN;
  const prevValueUS = totalCurrentUS - totalDayChangeUS;
  const totalDayChangePctIN = prevValueIN > 0 ? (totalDayChangeIN / prevValueIN) * 100 : 0;
  const totalDayChangePctUS = prevValueUS > 0 ? (totalDayChangeUS / prevValueUS) * 100 : 0;

  return (
    <div className="space-y-6">
      <UnsupportedMarketNotice supported={["IN", "US"]} />
      <MarketDisclaimer market={market} />

      <div className="flex items-center flex-wrap gap-3">
        <Briefcase size={22} className="text-brand-500" />
        <div>
          <h1 className="text-2xl font-bold">Portfolio</h1>
          <p className="text-gray-400 text-sm">Track your holdings and live P&L</p>
        </div>
        <button
          onClick={() => setShowAddHolding(v => !v)}
          className={clsx(
            "ml-auto flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border transition-colors",
            showAddHolding
              ? "bg-brand-500/10 border-brand-500/40 text-brand-400"
              : "border-dark-border text-gray-400 hover:text-white hover:border-white/30"
          )}
        >
          <PlusCircle size={13} /> {showAddHolding ? "Close" : "Add Holding"}
        </button>
        <button
          onClick={() => setShowImport(true)}
          disabled={!mounted || !apiBase}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dark-border text-gray-400 hover:text-white hover:border-white/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Upload size={13} /> Import Portfolio
        </button>
        <button
          onClick={() => exportPortfolioToExcel(rows)}
          disabled={rows.length === 0}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dark-border text-gray-400 hover:text-white hover:border-white/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Download size={13} /> Export Portfolio
        </button>
        {holdings.length > 0 && (() => {
          // Market-aware, matching every other summary on this page (cards,
          // allocation chart) — this previously showed the combined IN+US
          // count regardless of which market was selected.
          const marketHoldingsCount = holdings.filter(h => h.market === market).length;
          return (
            <span className="flex items-center gap-1.5 text-xs text-gray-500">
              <Wifi size={12} className="text-green-500" />
              Tracking {marketHoldingsCount} holding{marketHoldingsCount !== 1 ? "s" : ""} · live prices
            </span>
          );
        })()}
      </div>

      {showImport && apiBase && (
        <ImportPortfolioModal
          userId={userId}
          defaultMarket={market}
          existingHoldings={holdings}
          onClose={() => setShowImport(false)}
          onImported={refetchHoldings}
        />
      )}

      {/* Add holding form — collapsible (Session 10 follow-up): defaults
          open only for a genuinely empty portfolio, toggled via the "Add
          Holding" button in the header row above instead of staying
          permanently visible for a returning user with existing holdings. */}
      {showAddHolding && (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
          <h2 className="font-semibold mb-4 text-sm text-gray-300">Add Holding</h2>
          <div className="flex flex-wrap gap-3 items-end">
            <div className="flex-1 min-w-32">
              <label className="text-xs text-gray-400 mb-1 block">Symbol</label>
              <StockSymbolField
                className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white font-mono font-bold text-sm outline-none focus:border-brand-500 uppercase"
                value={sym}
                onChange={setSym}
                onEnter={add}
                market={market}
                placeholder={market === "IN" ? "RELIANCE, TCS…" : "AAPL, MSFT…"}
                onSelect={(stock: StockResult) => {
                  setSym(stock.symbol.replace(/\.(NS|BO)$/, ""));
                  if (stock.market === "IN" || stock.market === "US") setMarket(stock.market);
                }}
              />
            </div>
            <div className="w-28">
              <label className="text-xs text-gray-400 mb-1 block">Qty / Shares</label>
              <input className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white text-sm outline-none focus:border-brand-500"
                placeholder="10" type="number" min="0" value={qty} onChange={e => setQty(e.target.value)} />
            </div>
            <div className="w-36">
              <label className="text-xs text-gray-400 mb-1 block">Avg Buy Price</label>
              <input className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white font-mono text-sm outline-none focus:border-brand-500"
                placeholder="150.00" type="number" min="0" step="0.01" value={avgPrice} onChange={e => setAvgPrice(e.target.value)} />
            </div>
            <button onClick={add} className="flex items-center gap-2 px-5 py-2 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors">
              <PlusCircle size={15} /> Add
            </button>
          </div>
          {error && <p className="text-bear text-xs mt-2">{error}</p>}
        </div>
      )}

      {/* Summary cards */}
      {holdings.length > 0 && (
        <div className="space-y-3">
          {/* Indian holdings summary — no market label here: the page only
              ever shows one market's data at a time (the same global market
              toggle already visible in the header), so relabeling it again
              per-section was pure duplication. */}
          {hasIN && (
            <div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {[
                  { label: "Holdings", value: String(holdings.filter(h => h.market === "IN").length), color: "text-white" },
                  { label: "Invested", value: `₹${totalInvestedIN.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Current Value", value: `₹${totalCurrentIN.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Day's P&L", value: `${totalDayChangeIN >= 0 ? "+" : ""}₹${Math.abs(totalDayChangeIN).toLocaleString("en-IN", { maximumFractionDigits: 0 })} (${totalDayChangePctIN >= 0 ? "+" : ""}${totalDayChangePctIN.toFixed(1)}%)`, color: totalDayChangeIN >= 0 ? "text-bull" : "text-bear" },
                  { label: "P&L", value: `${totalPLIN >= 0 ? "+" : ""}₹${Math.abs(totalPLIN).toLocaleString("en-IN", { maximumFractionDigits: 0 })} (${totalPLPctIN >= 0 ? "+" : ""}${totalPLPctIN.toFixed(1)}%)`, color: totalPLIN >= 0 ? "text-bull" : "text-bear" },
                ].map(c => (
                  <div key={c.label} className="bg-dark-card border border-dark-border rounded-2xl p-4">
                    <p className="text-xs text-gray-400 mb-1">{c.label}</p>
                    <p className={clsx("text-lg font-bold", c.color)}>{c.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* US holdings summary — no market label, see note above. */}
          {hasUS && (
            <div>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {[
                  { label: "Holdings", value: String(holdings.filter(h => h.market === "US").length), color: "text-white" },
                  { label: "Invested", value: `$${totalInvestedUS.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Current Value", value: `$${totalCurrentUS.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Day's P&L", value: `${totalDayChangeUS >= 0 ? "+" : ""}$${Math.abs(totalDayChangeUS).toLocaleString(undefined, { maximumFractionDigits: 0 })} (${totalDayChangePctUS >= 0 ? "+" : ""}${totalDayChangePctUS.toFixed(1)}%)`, color: totalDayChangeUS >= 0 ? "text-bull" : "text-bear" },
                  { label: "P&L", value: `${totalPLUS >= 0 ? "+" : ""}$${Math.abs(totalPLUS).toLocaleString(undefined, { maximumFractionDigits: 0 })} (${totalPLPctUS >= 0 ? "+" : ""}${totalPLPctUS.toFixed(1)}%)`, color: totalPLUS >= 0 ? "text-bull" : "text-bear" },
                ].map(c => (
                  <div key={c.label} className="bg-dark-card border border-dark-border rounded-2xl p-4">
                    <p className="text-xs text-gray-400 mb-1">{c.label}</p>
                    <p className={clsx("text-lg font-bold", c.color)}>{c.value}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Selected-market holdings count while prices are still loading (or none in this market) */}
          {!hasIN && !hasUS && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-dark-card border border-dark-border rounded-2xl p-4">
                <p className="text-xs text-gray-400 mb-1">Holdings</p>
                <p className="text-lg font-bold text-white">{holdings.filter(h => h.market === market).length}</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Allocation chart — filtered to the selected market, same as the
          summary cards and holdings tables. Mixing ₹ and $ values in one
          chart would make the percentages meaningless (₹ and $ amounts
          aren't comparable without FX conversion). */}
      {holdings.filter(h => h.market === market).length > 1 && (
        <PortfolioAllocationChart
          stockSlices={chartSlices}
          sectorSlices={sectorSlices}
          unresolvedSectorValue={unresolvedSectorValue}
          unresolvedSectorCount={unresolvedSectorCount}
          currency={currency(market)}
          mode={allocationMode}
          onModeChange={setAllocationMode}
        />
      )}

      {/* Holdings tables — split by market so ₹ and $ rows are never mixed */}
      {holdings.length === 0 ? (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-10 text-center text-gray-500 text-sm">
          No holdings yet — add your first stock above
        </div>
      ) : (
        <div className="space-y-5">
          {/* No market label on either table — see the summary cards note
              above, same reasoning applies here. */}
          {hasINHoldings && (
            <div>
              <HoldingsTable
                rows={inRows}
                currency="₹"
                onRemove={remove}
                onEdit={edit}
                groupBySector={effectiveAllocationMode === "sector"}
              />
            </div>
          )}
          {hasUSHoldings && (
            <div>
              <HoldingsTable
                rows={usRows}
                currency="$"
                onRemove={remove}
                onEdit={edit}
                groupBySector={effectiveAllocationMode === "sector"}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
