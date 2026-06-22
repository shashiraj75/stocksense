"use client";
import { useState, useEffect } from "react";
import { useQueries } from "@tanstack/react-query";
import { fetchQuote, fetchPrediction, Market } from "@/utils/api";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { SignalBadge } from "@/components/SignalBadge";
import Link from "next/link";
import clsx from "clsx";
import { PlusCircle, Trash2, TrendingUp, TrendingDown, Briefcase, Wifi, Pencil, Check, X } from "lucide-react";
import { PortfolioAllocationChart } from "@/components/PortfolioAllocationChart";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { StockSymbolField } from "@/components/StockSymbolField";
import type { StockResult } from "@/hooks/useStockSearch";

interface Holding {
  symbol: string;
  market: Market;
  qty: number;
  avgPrice: number;
}

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
}: { r: Row & { _idx: number }; currency: string; onRemove: (i: number) => void; onEdit: (i: number, updates: { qty: number; avgPrice: number }) => void }) {
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
    onEdit(r._idx, { qty: q, avgPrice: a });
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
            <button onClick={() => onRemove(r._idx)} className="p-1 rounded text-gray-500 hover:text-bear transition-colors"><Trash2 size={14} /></button>
          </div>
        )}
      </td>
    </tr>
  );
}

function HoldingsTable({
  rows, currency, onRemove, onEdit,
}: { rows: (Row & { _idx: number })[]; currency: string; onRemove: (i: number) => void; onEdit: (i: number, updates: { qty: number; avgPrice: number }) => void }) {
  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-dark-border text-gray-400 text-left">
              <th className="px-4 py-3 font-medium">Symbol</th>
              <th className="px-4 py-3 font-medium text-right">Qty</th>
              <th className="px-4 py-3 font-medium text-right">Avg Buy</th>
              <th className="px-4 py-3 font-medium text-right">Current</th>
              <th className="px-4 py-3 font-medium text-right">Invested</th>
              <th className="px-4 py-3 font-medium text-right">Value</th>
              <th className="px-4 py-3 font-medium text-right">P&L</th>
              <th className="px-4 py-3 font-medium text-right">P&L %</th>
              <th className="px-4 py-3 font-medium text-center">Signal</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <HoldingRow key={r._idx} r={r} currency={currency} onRemove={onRemove} onEdit={onEdit} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function load(): Holding[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
}
function save(h: Holding[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(h));
}

export default function PortfolioPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [sym, setSym] = useState("");
  const [market, setMarket] = useMarketPreference(["IN", "US"] as const, "IN");
  const [qty, setQty] = useState("");
  const [avgPrice, setAvgPrice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { setHoldings(load()); }, []);

  const quoteQueries = useQueries({
    queries: holdings.map(h => ({
      queryKey: ["quote", h.symbol, h.market],
      queryFn: () => fetchQuote(h.symbol, h.market),
      staleTime: 5 * 60_000,
    })),
  });

  const signalQueries = useQueries({
    queries: holdings.map(h => ({
      queryKey: ["prediction", h.symbol, h.market, "medium"],
      queryFn: () => fetchPrediction(h.symbol, h.market, "medium"),
      staleTime: 15 * 60_000,   // predictions cache for 15 min
      retry: 1,
    })),
  });

  const add = () => {
    setError("");
    if (!sym.trim()) return setError("Enter a symbol");
    if (!qty || isNaN(+qty) || +qty <= 0) return setError("Enter valid quantity");
    if (!avgPrice || isNaN(+avgPrice) || +avgPrice <= 0) return setError("Enter valid buy price");
    const updated = [...holdings, { symbol: sym.trim().toUpperCase(), market, qty: +qty, avgPrice: +avgPrice }];
    setHoldings(updated); save(updated);
    setSym(""); setQty(""); setAvgPrice("");
  };

  const remove = (i: number) => {
    const updated = holdings.filter((_, idx) => idx !== i);
    setHoldings(updated); save(updated);
  };

  const edit = (i: number, updates: { qty: number; avgPrice: number }) => {
    const updated = holdings.map((h, idx) => idx === i ? { ...h, ...updates } : h);
    setHoldings(updated); save(updated);
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

  const hasIN = totalInvestedIN > 0;
  const hasUS = totalInvestedUS > 0;
  const hasINHoldings = holdings.some(h => h.market === "IN");
  const hasUSHoldings = holdings.some(h => h.market === "US");
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
        {holdings.length > 0 && (
          <span className="ml-auto flex items-center gap-1.5 text-xs text-gray-500">
            <Wifi size={12} className="text-green-500" />
            Tracking {holdings.length} holding{holdings.length !== 1 ? "s" : ""} · live prices
          </span>
        )}
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
          {/* Total holdings count when no prices loaded yet */}
          {!hasIN && !hasUS && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-dark-card border border-dark-border rounded-2xl p-4">
                <p className="text-xs text-gray-400 mb-1">Holdings</p>
                <p className="text-lg font-bold text-white">{holdings.length}</p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Allocation chart */}
      {holdings.length > 1 && (hasIN || hasUS) && (
        <PortfolioAllocationChart
          slices={rows.map(r => ({
            symbol: r.symbol,
            value: r.current ?? 0,
            signal: r.signal,
          }))}
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
                rows={rows.map((r, i) => ({ ...r, _idx: i })).filter(r => r.market === "IN")}
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
                rows={rows.map((r, i) => ({ ...r, _idx: i })).filter(r => r.market === "US")}
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
