"use client";
import { useState, useEffect, useCallback } from "react";
import { useQueries } from "@tanstack/react-query";
import { fetchQuote, Market } from "@/utils/api";
import { Bell, BellRing, PlusCircle, Trash2, CheckCircle } from "lucide-react";
import clsx from "clsx";

interface Alert {
  id: string;
  symbol: string;
  market: Market;
  targetPrice: number;
  direction: "above" | "below";
  triggered: boolean;
  createdAt: string;
}

const STORAGE_KEY = "stocksense_alerts";
function load(): Alert[] { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { return []; } }
function save(a: Alert[]) { localStorage.setItem(STORAGE_KEY, JSON.stringify(a)); }

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [sym, setSym] = useState("");
  const [market, setMarket] = useState<Market>("IN");
  const [targetPrice, setTargetPrice] = useState("");
  const [direction, setDirection] = useState<"above" | "below">("above");
  const [error, setError] = useState("");

  useEffect(() => { setAlerts(load()); }, []);

  const quoteQueries = useQueries({
    queries: alerts.map(a => ({
      queryKey: ["quote", a.symbol, a.market],
      queryFn: () => fetchQuote(a.symbol, a.market),
      staleTime: 60_000,
      refetchInterval: 60_000, // check every minute
    })),
  });

  // Check if any alerts triggered
  const checkAlerts = useCallback(() => {
    let changed = false;
    const updated = alerts.map((a, i) => {
      if (a.triggered) return a;
      const price = quoteQueries[i]?.data?.price;
      if (!price) return a;
      const hit = a.direction === "above" ? price >= a.targetPrice : price <= a.targetPrice;
      if (hit) { changed = true; return { ...a, triggered: true }; }
      return a;
    });
    if (changed) { setAlerts(updated); save(updated); }
  }, [alerts, quoteQueries]);

  useEffect(() => { checkAlerts(); }, [quoteQueries.map(q => q.data?.price).join(",")]);

  const add = () => {
    setError("");
    if (!sym.trim()) return setError("Enter a symbol");
    if (!targetPrice || isNaN(+targetPrice) || +targetPrice <= 0) return setError("Enter a valid target price");
    const newAlert: Alert = {
      id: Date.now().toString(),
      symbol: sym.trim().toUpperCase(),
      market, targetPrice: +targetPrice, direction,
      triggered: false, createdAt: new Date().toISOString(),
    };
    const updated = [newAlert, ...alerts];
    setAlerts(updated); save(updated);
    setSym(""); setTargetPrice("");
  };

  const remove = (id: string) => {
    const updated = alerts.filter(a => a.id !== id);
    setAlerts(updated); save(updated);
  };

  const resetTrigger = (id: string) => {
    const updated = alerts.map(a => a.id === id ? { ...a, triggered: false } : a);
    setAlerts(updated); save(updated);
  };

  const currency = (m: Market) => m === "US" ? "$" : "₹";
  const triggered = alerts.filter(a => a.triggered);
  const active = alerts.filter(a => !a.triggered);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Bell size={22} className="text-brand-500" />
        <div>
          <h1 className="text-2xl font-bold">Price Alerts</h1>
          <p className="text-gray-400 text-sm">Get notified when a stock hits your target — checked every minute</p>
        </div>
        {triggered.length > 0 && (
          <span className="ml-auto flex items-center gap-1.5 px-3 py-1 rounded-full bg-bull/20 text-bull text-xs font-semibold">
            <BellRing size={13} /> {triggered.length} triggered
          </span>
        )}
      </div>

      {/* Add alert form */}
      <div className="bg-dark-card border border-dark-border rounded-2xl p-5">
        <h2 className="font-semibold mb-4 text-sm text-gray-300">New Alert</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-28">
            <label className="text-xs text-gray-400 mb-1 block">Symbol</label>
            <input className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white font-mono font-bold text-sm outline-none focus:border-brand-500 uppercase"
              placeholder="AAPL" value={sym} onChange={e => setSym(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && add()} />
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
          <div>
            <label className="text-xs text-gray-400 mb-1 block">Alert when price goes</label>
            <div className="flex gap-1">
              {(["above", "below"] as const).map(d => (
                <button key={d} onClick={() => setDirection(d)}
                  className={clsx("px-3 py-2 rounded-lg text-xs font-medium border capitalize transition-colors",
                    direction === d ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                  {d === "above" ? "↑ Above" : "↓ Below"}
                </button>
              ))}
            </div>
          </div>
          <div className="w-36">
            <label className="text-xs text-gray-400 mb-1 block">Target Price</label>
            <input className="w-full bg-dark-bg border border-dark-border rounded-xl px-3 py-2 text-white font-mono text-sm outline-none focus:border-brand-500"
              placeholder="200.00" type="number" min="0" step="0.01" value={targetPrice}
              onChange={e => setTargetPrice(e.target.value)} />
          </div>
          <button onClick={add} className="flex items-center gap-2 px-5 py-2 rounded-xl bg-brand-500 text-white text-sm font-medium hover:bg-brand-600 transition-colors">
            <PlusCircle size={15} /> Set Alert
          </button>
        </div>
        {error && <p className="text-bear text-xs mt-2">{error}</p>}
        <p className="text-xs text-gray-500 mt-3">Note: Alerts are checked while this tab is open. Prices update every 60 seconds from Yahoo Finance.</p>
      </div>

      {/* Triggered alerts */}
      {triggered.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-bull flex items-center gap-2"><BellRing size={15} /> Triggered Alerts</h2>
          {triggered.map((a, i) => {
            const qi = alerts.indexOf(a);
            const price = quoteQueries[qi]?.data?.price;
            return (
              <div key={a.id} className="flex items-center gap-4 bg-bull/10 border border-bull/30 rounded-xl px-5 py-3">
                <CheckCircle size={18} className="text-bull shrink-0" />
                <div className="flex-1">
                  <span className="font-mono font-bold text-white">{a.symbol}</span>
                  <span className="text-gray-400 text-sm ml-2">
                    hit {a.direction} {currency(a.market)}{a.targetPrice.toLocaleString()}
                    {price && <span className="ml-2 text-bull">Current: {currency(a.market)}{price.toLocaleString()}</span>}
                  </span>
                </div>
                <button onClick={() => resetTrigger(a.id)} className="text-xs text-gray-400 hover:text-white border border-dark-border px-3 py-1 rounded-lg transition-colors">Reset</button>
                <button onClick={() => remove(a.id)} className="text-gray-500 hover:text-bear transition-colors"><Trash2 size={14} /></button>
              </div>
            );
          })}
        </div>
      )}

      {/* Active alerts */}
      {alerts.length === 0 ? (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-10 text-center text-gray-500 text-sm">
          No alerts set — add your first alert above
        </div>
      ) : active.length === 0 ? null : (
        <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
          <div className="px-5 py-3 border-b border-dark-border">
            <h2 className="text-sm font-semibold text-gray-300">Active Alerts ({active.length})</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-dark-border text-gray-400 text-left">
                <th className="px-4 py-3 font-medium">Symbol</th>
                <th className="px-4 py-3 font-medium">Condition</th>
                <th className="px-4 py-3 font-medium text-right">Target</th>
                <th className="px-4 py-3 font-medium text-right">Current</th>
                <th className="px-4 py-3 font-medium text-right">Distance</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {active.map((a) => {
                const qi = alerts.indexOf(a);
                const price = quoteQueries[qi]?.data?.price;
                const dist = price ? ((a.targetPrice - price) / price * 100) : null;
                return (
                  <tr key={a.id} className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                    <td className="px-4 py-3 font-mono font-bold text-white">
                      {a.symbol} <span className="text-xs text-gray-500">{a.market === "US" ? "🇺🇸" : "🇮🇳"}</span>
                    </td>
                    <td className="px-4 py-3 text-gray-300">
                      <span className={clsx("text-xs font-medium px-2 py-0.5 rounded-full",
                        a.direction === "above" ? "bg-bull/20 text-bull" : "bg-bear/20 text-bear")}>
                        {a.direction === "above" ? "↑ Goes above" : "↓ Falls below"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{currency(a.market)}{a.targetPrice.toLocaleString()}</td>
                    <td className="px-4 py-3 text-right font-mono">
                      {quoteQueries[qi]?.isLoading ? <span className="text-gray-500 animate-pulse">…</span>
                        : price ? `${currency(a.market)}${price.toLocaleString()}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-sm">
                      {dist !== null
                        ? <span className={clsx(Math.abs(dist) < 2 ? "text-yellow-400" : "text-gray-400")}>
                            {dist >= 0 ? "+" : ""}{dist.toFixed(1)}%
                          </span>
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => remove(a.id)} className="text-gray-500 hover:text-bear transition-colors">
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
