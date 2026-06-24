"use client";
import { useState, useEffect } from "react";
import { fetchQuote, fetchPrediction, Market, api } from "@/utils/api";
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

interface Holding {
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

type Row = Holding & {
  curPrice: number | null;
  invested: number;
  current: number | null;
  plAmt: number | null;
  plPct: number | null;
  loading: boolean;
  signal: string | null;
  confidence?: number;
  sigLoading: boolean;
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
      <td className={clsx("px-4 py-3 text-right font-mono font-bold",
        r.plAmt === null ? "text-gray-500" : r.plAmt >= 0 ? "text-bull" : "text-bear")}>
        {r.plAmt !== null ? `${r.plAmt >= 0 ? "+" : ""}${currency}${Math.abs(r.plAmt).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
      </td>
      <td className={clsx("px-4 py-3 text-right font-mono font-bold",
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
          <span className="text-gray-600 text-xs animate-pulse">…</span>
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

type SortKey = "symbol" | "qty" | "avgPrice" | "curPrice" | "invested" | "current" | "plAmt" | "plPct" | "signal";

const SORT_ACCESSORS: Record<SortKey, (r: Row) => string | number | null> = {
  symbol: (r) => r.symbol,
  qty: (r) => r.qty,
  avgPrice: (r) => r.avgPrice,
  curPrice: (r) => r.curPrice,
  invested: (r) => r.invested,
  current: (r) => r.current,
  plAmt: (r) => r.plAmt,
  plPct: (r) => r.plPct,
  signal: (r) => r.signal,
};

function SortableHeader({
  label, sortKey, align, activeKey, dir, onSort, title,
}: { label: string; sortKey: SortKey; align?: "right" | "center"; activeKey: SortKey | null; dir: "asc" | "desc"; onSort: (key: SortKey) => void; title?: string }) {
  const isActive = activeKey === sortKey;
  return (
    <th title={title} className={clsx("px-4 py-3 font-medium select-none", align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left")}>
      <button
        onClick={() => onSort(sortKey)}
        className={clsx(
          "flex items-center gap-1 hover:text-white transition-colors",
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

function HoldingsTable({
  rows, currency, onRemove, onEdit,
}: { rows: Row[]; currency: string; onRemove: (id: string) => void; onEdit: (id: string, updates: { qty: number; avgPrice: number }) => void }) {
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

  const sortedRows = sortKey ? [...rows].sort((a, b) => {
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
  }) : rows;

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-dark-border text-gray-400 text-left">
              <SortableHeader label="Symbol" sortKey="symbol" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Qty" sortKey="qty" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Avg Buy" sortKey="avgPrice" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Current" sortKey="curPrice" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Invested" sortKey="invested" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Value" sortKey="current" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="P&L" sortKey="plAmt" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="P&L %" sortKey="plPct" align="right" activeKey={sortKey} dir={sortDir} onSort={handleSort} />
              <SortableHeader label="Signal" sortKey="signal" align="center" activeKey={sortKey} dir={sortDir} onSort={handleSort}
                title="Today's forward-looking AI call for this stock — independent of your P&L. A BUY here doesn't retroactively justify your original entry price, and a HOLD/SELL doesn't mean you're wrong to be holding; it reflects current conditions, not your specific cost basis." />
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((r) => (
              <HoldingRow key={r.id} r={r} currency={currency} onRemove={onRemove} onEdit={onEdit} />
            ))}
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

  const [holdings, setHoldings] = useState<Holding[]>(() => loadLocal());
  const [sym, setSym] = useState("");
  const [market, setMarket] = useMarketPreference(["IN", "US"] as const, "IN");
  const [qty, setQty] = useState("");
  const [avgPrice, setAvgPrice] = useState("");
  const [error, setError] = useState("");
  const [showImport, setShowImport] = useState(false);

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

  const signalQueries = useStaggeredQueries(
    holdings.map(h => ({
      queryKey: ["prediction", h.symbol, h.market, "medium"],
      queryFn: () => fetchPrediction(h.symbol, h.market, "medium"),
      staleTime: 15 * 60_000,   // predictions cache for 15 min
      retry: 1,
    })),
    8 // load-tested: 8 concurrent fresh predictions resolve in ~12s with no
      // degradation vs 5 in ~9s — was 6 mainly out of caution before the
      // event-loop fixes; raised to cut the number of sequential batches a
      // large portfolio needs (e.g. 38 holdings: 7 batches -> 5).
  );

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

  // Compute totals per currency — never mix ₹ and $ into one number
  let totalInvestedIN = 0, totalCurrentIN = 0;
  let totalInvestedUS = 0, totalCurrentUS = 0;

  const rows = holdings.map((h, i) => {
    const q = quoteQueries[i]?.data;
    const curPrice = q?.price ?? null;
    const invested = h.qty * h.avgPrice;
    const current = curPrice ? h.qty * curPrice : null;
    const plAmt = current !== null ? current - invested : null;
    const plPct = plAmt !== null ? (plAmt / invested) * 100 : null;
    if (current !== null) {
      if (h.market === "IN") { totalInvestedIN += invested; totalCurrentIN += current; }
      else { totalInvestedUS += invested; totalCurrentUS += current; }
    }
    const sig = signalQueries[i]?.data;
    const signal = sig?.signal ?? null;
    const confidence = sig?.confidence ?? undefined;
    return { ...h, curPrice, invested, current, plAmt, plPct, loading: quoteQueries[i]?.isLoading, signal, confidence, sigLoading: signalQueries[i]?.isLoading };
  });

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

  return (
    <div className="space-y-6">
      <MarketDisclaimer market={market} />

      <div className="flex items-center gap-3">
        <Briefcase size={22} className="text-brand-500" />
        <div>
          <h1 className="text-2xl font-bold">Portfolio</h1>
          <p className="text-gray-400 text-sm">Track your holdings and live P&L</p>
        </div>
        <button
          onClick={() => setShowImport(true)}
          disabled={!apiBase}
          className="ml-auto flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-dark-border text-gray-400 hover:text-white hover:border-white/30 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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
        {holdings.length > 0 && (
          <span className="flex items-center gap-1.5 text-xs text-gray-500">
            <Wifi size={12} className="text-green-500" />
            Tracking {holdings.length} holding{holdings.length !== 1 ? "s" : ""} · live prices
          </span>
        )}
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

      {/* Add holding form */}
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
              onSelect={(stock: StockResult) => {
                setSym(stock.symbol.replace(/\.(NS|BO)$/, ""));
                if (stock.market === "IN" || stock.market === "US") setMarket(stock.market);
              }}
            />
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Market</label>
            <div className="flex gap-1">
              {(["IN", "US"] as Market[]).map(m => (
                <button key={m} onClick={() => setMarket(m)}
                  className={clsx("px-3 py-2 rounded-lg text-xs font-medium border transition-colors",
                    market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                  {m === "US" ? "🇺🇸" : "🇮🇳"} {m}
                </button>
              ))}
            </div>
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

      {/* Summary cards */}
      {holdings.length > 0 && (
        <div className="space-y-3">
          {/* Indian holdings summary */}
          {hasIN && (
            <div>
              <p className="text-xs text-gray-500 mb-2 flex items-center gap-1">🇮🇳 Indian Holdings (₹)</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: "Holdings", value: String(holdings.filter(h => h.market === "IN").length), color: "text-white" },
                  { label: "Invested", value: `₹${totalInvestedIN.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Current Value", value: `₹${totalCurrentIN.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, color: "text-white" },
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
          {/* US holdings summary */}
          {hasUS && (
            <div>
              <p className="text-xs text-gray-500 mb-2 flex items-center gap-1">🇺🇸 US Holdings ($)</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: "Holdings", value: String(holdings.filter(h => h.market === "US").length), color: "text-white" },
                  { label: "Invested", value: `$${totalInvestedUS.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
                  { label: "Current Value", value: `$${totalCurrentUS.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
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
          slices={rows.filter(r => r.market === market).map(r => ({
            symbol: r.symbol,
            value: r.current ?? 0,
            signal: r.signal,
          }))}
        />
      )}

      {/* Holdings tables — split by market so ₹ and $ rows are never mixed */}
      {holdings.length === 0 ? (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-10 text-center text-gray-500 text-sm">
          No holdings yet — add your first stock above
        </div>
      ) : (
        <div className="space-y-5">
          {hasINHoldings && (
            <div>
              <p className="text-xs text-gray-500 mb-2 flex items-center gap-1">🇮🇳 Indian Holdings (₹)</p>
              <HoldingsTable
                rows={rows.filter(r => r.market === "IN")}
                currency="₹"
                onRemove={remove}
                onEdit={edit}
              />
            </div>
          )}
          {hasUSHoldings && (
            <div>
              <p className="text-xs text-gray-500 mb-2 flex items-center gap-1">🇺🇸 US Holdings ($)</p>
              <HoldingsTable
                rows={rows.filter(r => r.market === "US")}
                currency="$"
                onRemove={remove}
                onEdit={edit}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
