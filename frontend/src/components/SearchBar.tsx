"use client";
import { useState, useCallback, useRef } from "react";
import { Search } from "lucide-react";
import { searchStocks } from "@/utils/api";
import { useRouter } from "next/navigation";

type SearchResult = { symbol: string; name: string; market?: string };

const MARKET_BADGE: Record<string, string> = {
  US: "🇺🇸",
  IN: "🇮🇳",
  CRYPTO: "₿",
};

export function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const router = useRouter();

  const handleChange = useCallback((v: string) => {
    setQuery(v);
    clearTimeout(timer.current);
    if (v.length < 1) { setResults([]); setOpen(false); return; }
    timer.current = setTimeout(async () => {
      const r = await searchStocks(v, "ALL");
      setResults(r.slice(0, 8));
      setOpen(true);
    }, 150);
  }, []);

  const pick = (result: SearchResult) => {
    const mkt = result.market ?? (
      result.symbol.endsWith(".NS") || result.symbol.endsWith(".BO") ? "IN" : "US"
    );
    const clean = result.symbol.replace(/\.(NS|BO)$/, "");
    router.push(`/stock/${clean}?market=${mkt}`);
    setQuery(""); setOpen(false); setResults([]);
  };

  return (
    <div className="relative w-full max-w-md">
      <div className="flex items-center gap-2 bg-dark-card border border-dark-border rounded-xl px-4 py-2.5">
        <Search size={16} className="text-gray-400" />
        <input
          className="bg-transparent text-white text-sm outline-none flex-1 placeholder:text-gray-500"
          placeholder="Search stocks… (AAPL, RELIANCE, BTC)"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onFocus={() => results.length > 0 && setOpen(true)}
        />
      </div>
      {open && results.length > 0 && (
        <ul className="absolute top-full mt-2 w-full bg-dark-card border border-dark-border rounded-xl overflow-hidden z-50 shadow-xl">
          {results.map((r) => (
            <li key={`${r.symbol}-${r.market}`}>
              <button
                onMouseDown={() => pick(r)}
                className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center gap-3"
              >
                <span className="text-base w-5 text-center flex-shrink-0">
                  {MARKET_BADGE[r.market ?? "US"] ?? "🌐"}
                </span>
                <span className="text-white font-mono font-bold text-sm flex-shrink-0">
                  {r.symbol.replace(/\.(NS|BO)$/, "")}
                </span>
                <span className="text-gray-400 text-xs truncate">{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
