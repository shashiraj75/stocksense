"use client";
import { useState, useCallback, useRef } from "react";
import { Search } from "lucide-react";
import { searchStocks } from "@/utils/api";
import { useRouter } from "next/navigation";

export function SearchBar() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ symbol: string; name: string }[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const router = useRouter();

  const handleChange = useCallback((v: string) => {
    setQuery(v);
    clearTimeout(timer.current);
    if (v.length < 1) { setResults([]); setOpen(false); return; }
    timer.current = setTimeout(async () => {
      const r = await searchStocks(v);
      setResults(r.slice(0, 6));
      setOpen(true);
    }, 300);
  }, []);

  const pick = (symbol: string) => {
    const market = symbol.endsWith(".NS") || symbol.endsWith(".BO") ? "IN" : "US";
    const clean = symbol.replace(/\.(NS|BO)$/, "");
    router.push(`/stock/${clean}?market=${market}`);
    setQuery(""); setOpen(false);
  };

  return (
    <div className="relative w-full max-w-md">
      <div className="flex items-center gap-2 bg-dark-card border border-dark-border rounded-xl px-4 py-2.5">
        <Search size={16} className="text-gray-400" />
        <input
          className="bg-transparent text-white text-sm outline-none flex-1 placeholder:text-gray-500"
          placeholder="Search stocks… (AAPL, RELIANCE)"
          value={query}
          onChange={(e) => handleChange(e.target.value)}
        />
      </div>
      {open && results.length > 0 && (
        <ul className="absolute top-full mt-2 w-full bg-dark-card border border-dark-border rounded-xl overflow-hidden z-50 shadow-xl">
          {results.map((r) => (
            <li key={r.symbol}>
              <button
                onClick={() => pick(r.symbol)}
                className="w-full text-left px-4 py-3 hover:bg-dark-border transition-colors flex items-center justify-between"
              >
                <span className="text-white font-mono font-bold text-sm">{r.symbol}</span>
                <span className="text-gray-400 text-xs truncate ml-3">{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
