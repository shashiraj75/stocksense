"use client";
import { useState, useCallback, useRef, useEffect } from "react";
import { Search } from "lucide-react";
import { useRouter } from "next/navigation";

type Stock = { symbol: string; name: string; market: string };
type Universe = { US: Stock[]; IN: Stock[]; CRYPTO: Stock[] };

const MARKET_BADGE: Record<string, string> = {
  US: "🇺🇸",
  IN: "🇮🇳",
  CRYPTO: "₿",
};

// Loaded once at module level, shared across all instances
let universeCache: Universe | null = null;
let loadPromise: Promise<Universe> | null = null;

function loadUniverse(): Promise<Universe> {
  if (universeCache) return Promise.resolve(universeCache);
  if (loadPromise) return loadPromise;
  loadPromise = fetch("/stock_universe.json")
    .then((r) => r.json())
    .then((data) => { universeCache = data; return data; });
  return loadPromise;
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

    if (sl === ql)            exact.push(s);
    else if (sl.startsWith(ql)) symStart.push(s);
    else if (sl.includes(ql))   symContain.push(s);
    else if (nl.startsWith(q))  nameStart.push(s);
    else if (nl.includes(q))    nameContain.push(s);
    else continue;
    seen.add(key);
  }

  return [...exact, ...symStart, ...symContain, ...nameStart, ...nameContain].slice(0, limit);
}

export function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Stock[]>([]);
  const [open, setOpen] = useState(false);
  const [universe, setUniverse] = useState<Universe | null>(universeCache);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const router = useRouter();

  // Load universe on mount (instant if already cached)
  useEffect(() => {
    if (!universe) loadUniverse().then(setUniverse);
  }, []);

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

  // Direct symbol fallback shown when no results found
  const showFallback = open && results.length === 0 && query.length >= 1;

  // Re-run search once universe loads (if user already typed something)
  useEffect(() => {
    if (universe && query.length > 0) {
      const r = searchLocal(universe, query);
      setResults(r);
      setOpen(r.length > 0);
    }
  }, [universe]);

  const pick = (result: Stock) => {
    const clean = result.symbol.replace(/\.(NS|BO)$/, "");
    router.push(`/stock/${clean}?market=${result.market}`);
    setQuery(""); setOpen(false); setResults([]);
  };

  const handleEnter = () => {
    if (results.length > 0) { pick(results[0]); return; }
    if (!query.trim()) return;
    const sym = query.trim().toUpperCase();
    // Look up in universe first so market is always correct
    if (universe) {
      const inMatch = universe.IN.find(s => s.symbol.replace(/\.(NS|BO)$/, "") === sym || s.symbol === sym);
      if (inMatch) { pick(inMatch); return; }
      const cryptoMatch = universe.CRYPTO.find(s => s.symbol === sym);
      if (cryptoMatch) { pick(cryptoMatch); return; }
      const usMatch = universe.US.find(s => s.symbol === sym);
      if (usMatch) { pick(usMatch); return; }
    }
    // Final fallback — default to US
    pick({ symbol: sym, name: sym, market: "US" });
  };

  return (
    <div className="relative w-full max-w-xs">
      <div className="flex items-center gap-2 bg-dark-card border border-dark-border rounded-xl px-4 py-2.5">
        <Search size={16} className="text-gray-400" />
        <input
          className="bg-transparent text-white text-sm outline-none flex-1 placeholder:text-gray-500"
          placeholder="Search stocks… (AAPL, RELIANCE, BTC)"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onFocus={() => results.length > 0 && setOpen(true)}
          onKeyDown={(e) => e.key === "Enter" && handleEnter()}
        />
      </div>
      {(open && results.length > 0) || showFallback ? (
        <ul className="absolute top-full mt-2 w-full bg-dark-card border border-dark-border rounded-xl overflow-hidden z-50 shadow-xl">
          {results.map((r) => (
            <li key={`${r.symbol}-${r.market}`}>
              <button
                onMouseDown={() => pick(r)}
                className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center gap-3"
              >
                <span className="text-base w-5 text-center flex-shrink-0">
                  {MARKET_BADGE[r.market] ?? "🌐"}
                </span>
                <span className="text-white font-mono font-bold text-sm flex-shrink-0">
                  {r.symbol.replace(/\.(NS|BO)$/, "")}
                </span>
                <span className="text-gray-400 text-xs truncate">{r.name}</span>
              </button>
            </li>
          ))}
          {showFallback && (
            <>
              {(["US", "IN", "CRYPTO"] as const).map((mkt) => (
                <li key={mkt}>
                  <button
                    onMouseDown={() => pick({ symbol: query.toUpperCase(), name: query.toUpperCase(), market: mkt })}
                    className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center gap-3"
                  >
                    <span className="text-base w-5 text-center flex-shrink-0">{MARKET_BADGE[mkt]}</span>
                    <span className="text-white font-mono font-bold text-sm flex-shrink-0">{query.toUpperCase()}</span>
                    <span className="text-gray-400 text-xs">Search in {mkt === "IN" ? "NSE India" : mkt === "CRYPTO" ? "Crypto" : "US Market"} →</span>
                  </button>
                </li>
              ))}
            </>
          )}
        </ul>
      ) : null}
    </div>
  );
}
