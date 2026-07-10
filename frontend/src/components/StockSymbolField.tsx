"use client";
import { useState } from "react";
import { useStockSearch, type StockResult } from "@/hooks/useStockSearch";

const MARKET_BADGE: Record<string, string> = { US: "🇺🇸", IN: "🇮🇳", CRYPTO: "₿" };

/**
 * Symbol text input with the same predictive autocomplete dropdown used by
 * the global SearchBar and Watchlist's "add stock" field — Portfolio and
 * Alerts previously had a bare <input> with no matching, unlike every other
 * place in the app a symbol gets typed.
 *
 * Calling code keeps owning the symbol/market state — this just supplies the
 * dropdown and reports back via onSelect when the user picks a real match
 * (which also carries the correct market, so the caller can sync its own
 * market toggle instead of requiring the user to set it manually).
 *
 * `market`, when passed, narrows the dropdown to just that market's symbols
 * (e.g. Portfolio's Add Holding, where a result from the other market can't
 * actually be added under the currently-selected Market toggle — showing it
 * anyway was confusing, not just noisy). Omit it to keep the original
 * any-market behavior (global SearchBar, Watchlist).
 */
export function StockSymbolField({
  value,
  onChange,
  onSelect,
  onEnter,
  market,
  placeholder = "AAPL, RELIANCE, BTC",
  className = "",
}: {
  value: string;
  onChange: (v: string) => void;
  onSelect: (stock: StockResult) => void;
  onEnter?: () => void;
  market?: string;
  placeholder?: string;
  className?: string;
}) {
  const { results, open, setOpen, handleChange } = useStockSearch(market);
  // A confirmation preview of the exact match the user picked — cleared as
  // soon as they type again, so it never lingers as stale/misleading info
  // once the field no longer reflects that selection.
  const [preview, setPreview] = useState<StockResult | null>(null);

  return (
    <div className="relative">
      <input
        className={className}
        placeholder={placeholder}
        value={value}
        onChange={e => {
          const v = e.target.value.toUpperCase();
          onChange(v);
          handleChange(v);
          setPreview(null);
        }}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={e => {
          if (e.key !== "Enter") return;
          if (results.length > 0) {
            onSelect(results[0]);
            setPreview(results[0]);
            setOpen(false);
            return;
          }
          onEnter?.();
        }}
      />
      {preview && (
        <p className="mt-1 text-xs text-gray-500 flex items-center gap-1.5">
          <span className="w-4 text-center shrink-0">{MARKET_BADGE[preview.market] ?? "🌐"}</span>
          <span className="truncate">{preview.name}</span>
        </p>
      )}
      {open && results.length > 0 && (
        <ul className="absolute top-full mt-1.5 w-[max(100%,240px)] bg-dark-card border border-dark-border rounded-xl overflow-hidden z-50 shadow-xl">
          {results.map(r => (
            <li key={`${r.symbol}-${r.market}`}>
              <button
                onMouseDown={() => { onSelect(r); setPreview(r); }}
                className="w-full text-left px-3 py-2 hover:bg-dark-border transition-colors flex items-center gap-2"
              >
                <span className="text-sm w-4 text-center shrink-0">{MARKET_BADGE[r.market] ?? "🌐"}</span>
                <span className="text-white font-mono font-bold text-xs shrink-0">
                  {r.symbol.replace(/\.(NS|BO)$/, "")}
                </span>
                <span className="text-gray-400 text-xs truncate min-w-0 flex-1">{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
