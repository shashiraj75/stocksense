"use client";
import { useState, useRef, useEffect } from "react";
import { usePathname } from "next/navigation";
import { ChevronDown, Check } from "lucide-react";
import clsx from "clsx";
import { useGlobalMarketContext, GLOBAL_MARKET_CONTEXTS, type GlobalMarketContext } from "@/hooks/useGlobalMarketContext";

// Which of the four global contexts each page currently has real content
// for — used only to show a "Coming soon" hint in the dropdown itself.
// Selecting an unsupported context here is never blocked; the page itself
// (see UnsupportedMarketNotice) is responsible for degrading gracefully.
// Pages not listed here (Dashboard, the public landing page, etc.) support
// everything the dropdown can offer, so no entry is needed for them.
const PAGE_SUPPORT: Record<string, GlobalMarketContext[]> = {
  "/picks": ["IN", "US"],
  "/multibagger": ["IN", "US"],
  "/portfolio": ["IN", "US"],
  "/paper-trading": ["IN", "US"],
  "/screener": ["IN", "US"],
  "/heatmap": ["IN", "US"],
  "/backtest": ["IN", "US"],
  "/alerts": ["IN", "US"],
};

function currentPageSupports(pathname: string | null, key: GlobalMarketContext): boolean {
  if (!pathname) return true;
  const entry = Object.entries(PAGE_SUPPORT).find(([prefix]) => pathname.startsWith(prefix));
  if (!entry) return true; // page not in the map — assume it supports everything the dropdown offers
  return entry[1].includes(key);
}

/**
 * The single, persistent global market-context control — replaces having to
 * hunt for an IN/US toggle on each individual page to know what you're
 * looking at. Reads/writes the same shared `ss_market_pref` localStorage
 * key every page-level useMarketPreference() toggle already uses, so
 * picking a market here instantly updates any already-open page (Daily
 * Picks, Multibagger, Portfolio, Paper Trading, etc.) without a reload.
 */
export function GlobalMarketDropdown() {
  const [context, setContext] = useGlobalMarketContext();
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const ref = useRef<HTMLDivElement>(null);

  const active = GLOBAL_MARKET_CONTEXTS.find(c => c.key === context) ?? GLOBAL_MARKET_CONTEXTS[0];

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    if (open) {
      document.addEventListener("mousedown", handleClick);
      document.addEventListener("keydown", handleKey);
    }
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Select market context"
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm font-medium text-gray-200 border border-dark-border bg-dark-card hover:border-white/30 hover:text-white transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-400"
      >
        <span>{active.emoji}</span>
        <span className="hidden sm:inline">{active.label}</span>
        <ChevronDown size={13} className={clsx("transition-transform text-gray-500", open && "rotate-180")} />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute right-0 top-full mt-2 w-48 bg-dark-card border border-dark-border rounded-xl shadow-xl overflow-hidden z-50"
        >
          {GLOBAL_MARKET_CONTEXTS.map(({ key, emoji, label }) => {
            const isActive = key === context;
            const supported = currentPageSupports(pathname, key);
            return (
              <button
                key={key}
                type="button"
                role="option"
                aria-selected={isActive}
                onClick={() => {
                  setContext(key);
                  setOpen(false);
                }}
                className={clsx(
                  "w-full flex items-center justify-between gap-2 px-3.5 py-2.5 text-sm text-left border-b border-dark-border last:border-0 transition-colors hover:bg-dark-border/50",
                  isActive ? "font-semibold text-white bg-white/5" : "text-gray-300"
                )}
              >
                <span className="flex items-center gap-2">
                  <span>{emoji}</span>
                  <span>{label}</span>
                  {!supported && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-dark-border/60 text-gray-500">Coming soon</span>
                  )}
                </span>
                {isActive && <Check size={14} className="text-brand-400 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
