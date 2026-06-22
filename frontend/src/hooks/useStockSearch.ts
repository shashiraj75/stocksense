"use client";
import { useState, useCallback, useRef, useEffect } from "react";

// Was previously duplicated nearly verbatim in SearchBar.tsx and
// watchlist/page.tsx — extracted here so Portfolio and Alerts (which only
// had a bare text input, no predictive matching) can get the same
// autocomplete behavior without a third copy-paste.
export type StockResult = { symbol: string; name: string; market: string };
type Universe = { US: StockResult[]; IN: StockResult[]; CRYPTO: StockResult[] };

let _universeCache: Universe | null = null;
let _loadPromise: Promise<Universe> | null = null;

function loadUniverse(): Promise<Universe> {
  if (_universeCache) return Promise.resolve(_universeCache);
  if (_loadPromise) return _loadPromise;
  _loadPromise = fetch("/stock_universe.json").then(r => r.json()).then(d => { _universeCache = d; return d; });
  return _loadPromise;
}

function searchLocal(universe: Universe, query: string, limit = 8): StockResult[] {
  const q = query.toLowerCase().trim();
  if (!q) return [];

  const all = [...universe.US, ...universe.IN, ...universe.CRYPTO];
  const exact: StockResult[] = [], symStart: StockResult[] = [], symContain: StockResult[] = [],
        nameStart: StockResult[] = [], nameContain: StockResult[] = [];
  const seen = new Set<string>();

  for (const s of all) {
    const key = `${s.symbol}:${s.market}`;
    if (seen.has(key)) continue;
    const sl = s.symbol.toLowerCase().replace(/-/g, ".");
    const nl = s.name.toLowerCase();
    const ql = q.replace(/-/g, ".");

    if (sl === ql)              exact.push(s);
    else if (sl.startsWith(ql)) symStart.push(s);
    else if (sl.includes(ql))   symContain.push(s);
    else if (nl.startsWith(q))  nameStart.push(s);
    else if (nl.includes(q))    nameContain.push(s);
    else continue;
    seen.add(key);
  }

  return [...exact, ...symStart, ...symContain, ...nameStart, ...nameContain].slice(0, limit);
}

export function useStockSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockResult[]>([]);
  const [open, setOpen] = useState(false);
  const [universe, setUniverse] = useState<Universe | null>(_universeCache);
  const timer = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => { if (!universe) loadUniverse().then(setUniverse); }, []);

  // Re-run search once universe finishes loading, in case the user already typed
  useEffect(() => {
    if (universe && query.length > 0) {
      setResults(searchLocal(universe, query));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [universe]);

  const handleChange = useCallback((v: string) => {
    setQuery(v);
    clearTimeout(timer.current);
    if (v.length < 1) { setResults([]); setOpen(false); return; }
    timer.current = setTimeout(() => {
      if (!universe) return;
      setResults(searchLocal(universe, v));
      setOpen(true);
    }, 100);
  }, [universe]);

  const reset = useCallback(() => { setQuery(""); setResults([]); setOpen(false); }, []);

  return { query, setQuery, results, open, setOpen, handleChange, reset, universe };
}
