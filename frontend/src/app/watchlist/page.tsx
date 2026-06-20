"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, Market, fetchQuote } from "@/utils/api";
import { Trash2, TrendingUp, TrendingDown, Minus, Search } from "lucide-react";
import Link from "next/link";
import clsx from "clsx";
import { useAuth } from "@/lib/AuthContext";

interface WatchlistItem { symbol: string; market: Market; notes: string; }

// ── Stock universe search (same logic as global SearchBar) ──────────────────
type Stock = { symbol: string; name: string; market: string };
type Universe = { US: Stock[]; IN: Stock[]; CRYPTO: Stock[] };

const MARKET_BADGE: Record<string, string> = { US: "🇺🇸", IN: "🇮🇳", CRYPTO: "₿" };

let _universeCache: Universe | null = null;
let _loadPromise: Promise<Universe> | null = null;
function loadUniverse(): Promise<Universe> {
  if (_universeCache) return Promise.resolve(_universeCache);
  if (_loadPromise) return _loadPromise;
  _loadPromise = fetch("/stock_universe.json").then(r => r.json()).then(d => { _universeCache = d; return d; });
  return _loadPromise;
}
function searchLocal(universe: Universe, query: string, limit = 8): Stock[] {
  const q = query.toLowerCase().trim();
  if (!q) return [];
  const all = [...universe.US, ...universe.IN, ...universe.CRYPTO];
  const exact: Stock[] = [], symStart: Stock[] = [], symContain: Stock[] = [],
        nameStart: Stock[] = [], nameContain: Stock[] = [];
  const seen = new Set<string>();
  for (const s of all) {
    const key = `${s.symbol}:${s.market}`;
    if (seen.has(key)) continue;
    const sl = s.symbol.toLowerCase().replace(/-/g, ".");
    const nl = s.name.toLowerCase();
    const ql = q.replace(/-/g, ".");
    if (sl === ql) exact.push(s);
    else if (sl.startsWith(ql)) symStart.push(s);
    else if (sl.includes(ql))   symContain.push(s);
    else if (nl.startsWith(q))  nameStart.push(s);
    else if (nl.includes(q))    nameContain.push(s);
    else continue;
    seen.add(key);
  }
  return [...exact, ...symStart, ...symContain, ...nameStart, ...nameContain].slice(0, limit);
}
// ────────────────────────────────────────────────────────────────────────────

export default function WatchlistPage() {
  const { user } = useAuth();
  const userId = user?.id ?? "";
  const qc = useQueryClient();

  // Search state
  const [query, setQuery]       = useState("");
  const [results, setResults]   = useState<Stock[]>([]);
  const [open, setOpen]         = useState(false);
  const [universe, setUniverse] = useState<Universe | null>(_universeCache);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => { if (!universe) loadUniverse().then(setUniverse); }, []);
  useEffect(() => {
    if (universe && query.length > 0) {
      const r = searchLocal(universe, query);
      setResults(r);
      setOpen(r.length > 0 || query.length >= 1);
    }
  }, [universe, query]);

  const handleChange = useCallback((v: string) => {
    setQuery(v);
    clearTimeout(timer.current);
    if (v.length < 1) { setResults([]); setOpen(false); return; }
    timer.current = setTimeout(() => {
      if (!universe) return;
      const r = searchLocal(universe, v);
      setResults(r);
      setOpen(true);
    }, 100);
  }, [universe]);

  const { data, isLoading } = useQuery({
    queryKey: ["watchlist", userId],
    queryFn: () => api.get<{ items: WatchlistItem[] }>(`/api/watchlist/${userId}`).then(r => r.data),
    enabled: !!userId,
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });

  const items = data?.items ?? [];

  const quoteQueries = useQueries({
    queries: items.map(item => ({
      queryKey: ["quote", item.symbol, item.market],
      queryFn: () => fetchQuote(item.symbol, item.market),
      staleTime: 60_000,      // refresh every 60s during market hours
      retry: 1,
    })),
  });

  const add = useMutation({
    mutationFn: (item: WatchlistItem) => api.post(`/api/watchlist/${userId}`, item).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", userId] }),
  });

  const remove = useMutation({
    mutationFn: (symbol: string) => api.delete(`/api/watchlist/${userId}/${symbol}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist", userId] }),
  });

  const pickStock = (stock: Stock) => {
    const sym = stock.symbol.replace(/\.(NS|BO)$/, "");
    add.mutate({ symbol: sym, market: stock.market as Market, notes: "" });
    setQuery(""); setOpen(false); setResults([]);
  };

  const handleEnter = () => {
    if (results.length > 0) { pickStock(results[0]); return; }
    if (!query.trim()) return;
    const sym = query.trim().toUpperCase();
    if (universe) {
      const inMatch = universe.IN.find(s => s.symbol.replace(/\.(NS|BO)$/, "") === sym || s.symbol === sym);
      if (inMatch) { pickStock(inMatch); return; }
      const cryptoMatch = universe.CRYPTO.find(s => s.symbol === sym);
      if (cryptoMatch) { pickStock(cryptoMatch); return; }
      const usMatch = universe.US.find(s => s.symbol === sym);
      if (usMatch) { pickStock(usMatch); return; }
    }
    // fallback — assume IN
    pickStock({ symbol: sym, name: sym, market: "IN" });
  };

  const showFallback = open && results.length === 0 && query.length >= 1;

  const currency = (market: Market) => market === "IN" ? "₹" : "$";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Watchlist</h1>
        <p className="text-gray-400 text-sm mt-1">Track your favourite stocks</p>
      </div>

      {/* Add stock — search with dropdown */}
      <div className="relative">
        <div className="flex items-center gap-2 bg-dark-card border border-dark-border rounded-xl px-4 py-2.5 focus-within:border-brand-500 transition-colors">
          <Search size={16} className="text-gray-400 shrink-0" />
          <input
            className="bg-transparent text-white text-sm outline-none flex-1 placeholder:text-gray-500"
            placeholder="Search stocks to add… (AAPL, RELIANCE, BTC)"
            value={query}
            onChange={e => handleChange(e.target.value)}
            onBlur={() => setTimeout(() => setOpen(false), 150)}
            onFocus={() => (results.length > 0 || query.length > 0) && setOpen(true)}
            onKeyDown={e => e.key === "Enter" && handleEnter()}
          />
          {add.isPending && <span className="text-xs text-gray-500 animate-pulse">Adding…</span>}
        </div>

        {/* Dropdown */}
        {((open && results.length > 0) || showFallback) && (
          <ul className="absolute top-full mt-2 w-full bg-dark-card border border-dark-border rounded-xl overflow-hidden z-50 shadow-xl">
            {results.map(r => (
              <li key={`${r.symbol}-${r.market}`}>
                <button
                  onMouseDown={() => pickStock(r)}
                  className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center gap-3"
                >
                  <span className="text-base w-5 text-center shrink-0">{MARKET_BADGE[r.market] ?? "🌐"}</span>
                  <span className="text-white font-mono font-bold text-sm shrink-0">{r.symbol.replace(/\.(NS|BO)$/, "")}</span>
                  <span className="text-gray-400 text-xs truncate">{r.name}</span>
                  <span className="ml-auto text-[10px] text-brand-400 font-medium shrink-0">+ Add</span>
                </button>
              </li>
            ))}
            {showFallback && (
              <>
                {(["IN", "US", "CRYPTO"] as const).map(mkt => (
                  <li key={mkt}>
                    <button
                      onMouseDown={() => pickStock({ symbol: query.toUpperCase(), name: query.toUpperCase(), market: mkt })}
                      className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center gap-3"
                    >
                      <span className="text-base w-5 text-center shrink-0">{MARKET_BADGE[mkt]}</span>
                      <span className="text-white font-mono font-bold text-sm shrink-0">{query.toUpperCase()}</span>
                      <span className="text-gray-400 text-xs">Add to {mkt === "IN" ? "NSE India" : mkt === "CRYPTO" ? "Crypto" : "US Market"}</span>
                      <span className="ml-auto text-[10px] text-brand-400 font-medium shrink-0">+ Add</span>
                    </button>
                  </li>
                ))}
              </>
            )}
          </ul>
        )}
      </div>

      {/* List */}
      <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
        {isLoading ? (
          <div className="p-6 text-gray-400 text-sm">Loading…</div>
        ) : !items.length ? (
          <div className="p-10 text-center text-gray-500 text-sm">
            No stocks in your watchlist yet. Add one above!
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-dark-border text-gray-400 text-left">
                <th className="px-6 py-4 font-medium">Symbol</th>
                <th className="px-6 py-4 font-medium">Market</th>
                <th className="px-6 py-4 font-medium text-right">Price</th>
                <th className="px-6 py-4 font-medium text-right">Change</th>
                <th className="px-6 py-4 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => {
                const q = quoteQueries[i]?.data;
                const loading = quoteQueries[i]?.isLoading;
                const price = q?.price ?? null;
                const changePct = q?.change_pct ?? null;
                const changeAmt = q?.change ?? null;
                const up = (changePct ?? 0) >= 0;

                return (
                  <tr key={item.symbol} className="border-b border-dark-border hover:bg-dark-border/30 transition-colors">
                    <td className="px-6 py-4 font-mono font-bold text-white">
                      <Link href={`/stock/${item.symbol}?market=${item.market}`} className="hover:text-brand-400 transition-colors">
                        {item.symbol}
                      </Link>
                    </td>
                    <td className="px-6 py-4 text-gray-400">{item.market === "US" ? "🇺🇸 USA" : "🇮🇳 India"}</td>
                    <td className="px-6 py-4 text-right font-mono font-bold text-white">
                      {loading ? (
                        <span className="text-gray-600 animate-pulse">—</span>
                      ) : price != null ? (
                        `${currency(item.market)}${price.toLocaleString()}`
                      ) : (
                        <span className="text-gray-600">N/A</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      {loading ? (
                        <span className="text-gray-600 animate-pulse">—</span>
                      ) : changePct != null ? (
                        <div className={clsx("flex items-center justify-end gap-1 font-medium", up ? "text-bull" : "text-bear")}>
                          {up ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
                          <span>{up ? "+" : ""}{changeAmt != null ? changeAmt.toFixed(2) : ""}</span>
                          <span className="text-xs">({up ? "+" : ""}{changePct.toFixed(2)}%)</span>
                        </div>
                      ) : (
                        <span className="text-gray-600 flex items-center justify-end gap-1"><Minus size={12} /> N/A</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link href={`/stock/${item.symbol}?market=${item.market}`}
                          className="px-3 py-1 rounded-lg bg-brand-500/20 text-brand-500 border border-brand-500/30 hover:bg-brand-500/30 text-xs font-medium transition-colors">
                          Analyse →
                        </Link>
                        <button onClick={() => remove.mutate(item.symbol)}
                          className="p-1.5 rounded-lg text-gray-500 hover:text-bear hover:bg-bear/10 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
