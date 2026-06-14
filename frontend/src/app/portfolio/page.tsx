"use client";
import { useState, useEffect } from "react";
import { useQueries } from "@tanstack/react-query";
import { fetchQuote, Market } from "@/utils/api";
import { MarketDisclaimer } from "@/components/MarketDisclaimer";
import { SignalBadge } from "@/components/SignalBadge";
import Link from "next/link";
import clsx from "clsx";
import { PlusCircle, Trash2, TrendingUp, TrendingDown, Briefcase } from "lucide-react";

interface Holding {
  symbol: string;
  market: Market;
  qty: number;
  avgPrice: number;
}

const STORAGE_KEY = "stocksense_portfolio";

function load(): Holding[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; }
}
function save(h: Holding[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(h));
}

export default function PortfolioPage() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [sym, setSym] = useState("");
  const [market, setMarket] = useState<Market>("US");
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

  const currency = (m: Market) => m === "US" ? "$" : "₹";

  // Compute totals
  let totalInvested = 0, totalCurrent = 0;
  const rows = holdings.map((h, i) => {
    const q = quoteQueries[i]?.data;
    const curPrice = q?.price ?? null;
    const invested = h.qty * h.avgPrice;
    const current = curPrice ? h.qty * curPrice : null;
    const plAmt = current !== null ? current - invested : null;
    const plPct = plAmt !== null ? (plAmt / invested) * 100 : null;
    if (current !== null) { totalInvested += invested; totalCurrent += current; }
    return { ...h, curPrice, invested, current, plAmt, plPct, loading: quoteQueries[i]?.isLoading };
  });

  const totalPL = totalCurrent - totalInvested;
  const totalPLPct = totalInvested > 0 ? (totalPL / totalInvested) * 100 : 0;

  return (
    <div className="space-y-6">
      <MarketDisclaimer market={market} />

      <div className="flex items-center gap-3">
        <Briefcase size={22} className="text-brand-500" />
        <div>
          <h1 className="text-2xl font-bold">Portfolio</h1>
          <p className="text-gray-400 text-sm">Track your holdings and live P&L</p>
        </div>
      </div>

      {/* Summary cards */}
      {holdings.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: "Holdings", value: holdings.length, color: "text-white" },
            { label: "Invested", value: `$${totalInvested.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
            { label: "Current Value", value: `$${totalCurrent.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-white" },
            { label: "Total P&L", value: `${totalPL >= 0 ? "+" : ""}$${totalPL.toLocaleString(undefined, { maximumFractionDigits: 0 })} (${totalPLPct >= 0 ? "+" : ""}${totalPLPct.toFixed(1)}%)`,
              color: totalPL >= 0 ? "text-bull" : "text-bear" },
          ].map(c => (
            <div key={c.label} className="bg-dark-card border border-dark-border rounded-2xl p-5">
              <p className="text-xs text-gray-400 mb-1">{c.label}</p>
              <p className={clsx("text-xl font-bold", c.color)}>{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Add holding form */}
      <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
        <h2 className="font-semibold mb-4 text-sm text-gray-300">Add Holding</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-32">
            <label className="text-xs text-gray-400 mb-1 block">Symbol</label>
            <input className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white font-mono font-bold text-sm outline-none focus:border-brand-500 uppercase"
              placeholder="AAPL" value={sym} onChange={e => setSym(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && add()} />
          </div>
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Market</label>
            <div className="flex gap-1">
              {(["US", "IN"] as Market[]).map(m => (
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

      {/* Holdings table */}
      {holdings.length === 0 ? (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-10 text-center text-gray-500 text-sm">
          No holdings yet — add your first stock above
        </div>
      ) : (
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
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                    <td className="px-4 py-3">
                      <Link href={`/stock/${r.symbol}?market=${r.market}`}
                        className="font-mono font-bold text-white hover:text-brand-500 transition-colors">
                        {r.symbol}
                      </Link>
                      <span className="ml-2 text-xs text-gray-500">{r.market === "US" ? "🇺🇸" : "🇮🇳"}</span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{r.qty}</td>
                    <td className="px-4 py-3 text-right font-mono">{currency(r.market)}{r.avgPrice.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {r.loading ? <span className="animate-pulse text-gray-500">…</span>
                        : r.curPrice ? `${currency(r.market)}${r.curPrice.toLocaleString()}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-gray-300">{currency(r.market)}{r.invested.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {r.current !== null ? `${currency(r.market)}${r.current.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                    </td>
                    <td className={clsx("px-4 py-3 text-right font-mono font-bold",
                      r.plAmt === null ? "text-gray-500" : r.plAmt >= 0 ? "text-bull" : "text-bear")}>
                      {r.plAmt !== null ? `${r.plAmt >= 0 ? "+" : ""}${currency(r.market)}${Math.abs(r.plAmt).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
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
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => remove(i)} className="text-gray-500 hover:text-bear transition-colors">
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
