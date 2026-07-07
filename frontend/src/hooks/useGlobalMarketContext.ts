"use client";
import { useState, useEffect, useCallback } from "react";

// Shared storage key with useMarketPreference — both read/write the exact
// same localStorage entry, so the header's global dropdown and every
// page-level IN/US toggle stay backed by one source of truth.
export const GLOBAL_MARKET_KEY = "ss_market_pref";

// The native `storage` event only fires in *other* tabs, never the tab that
// made the change — so a page-level toggle and the header dropdown, both
// mounted in the same tab, would otherwise drift out of sync until the next
// full reload. This custom event fires immediately in the same tab too.
export const GLOBAL_MARKET_EVENT = "ss-market-pref-changed";

export type GlobalMarketContext = "IN" | "US" | "CRYPTO" | "COMMODITY";

export const GLOBAL_MARKET_CONTEXTS: { key: GlobalMarketContext; emoji: string; label: string }[] = [
  { key: "IN", emoji: "🇮🇳", label: "India" },
  { key: "US", emoji: "🇺🇸", label: "US" },
  { key: "CRYPTO", emoji: "₿", label: "Crypto" },
  { key: "COMMODITY", emoji: "🥇", label: "Commodities" },
];

function readStored(): GlobalMarketContext {
  try {
    const stored = localStorage.getItem(GLOBAL_MARKET_KEY);
    if (stored && GLOBAL_MARKET_CONTEXTS.some(c => c.key === stored)) return stored as GlobalMarketContext;
  } catch {
    // localStorage unavailable (e.g. private browsing) — fall through to default
  }
  return "IN";
}

/**
 * The raw, page-agnostic global market context — always one of all four
 * values, regardless of whether the current page actually supports it.
 * Used by the header's GlobalMarketDropdown (which must always reflect and
 * set the true global selection) and by pages that want to render a
 * "coming soon" notice when the global selection is one they don't support
 * yet, rather than silently ignoring it.
 */
export function useGlobalMarketContext(): [GlobalMarketContext, (value: GlobalMarketContext) => void] {
  const [value, setValue] = useState<GlobalMarketContext>("IN");

  useEffect(() => {
    setValue(readStored());
    const onChange = (e: Event) => {
      const detail = (e as CustomEvent<GlobalMarketContext>).detail;
      setValue(detail && GLOBAL_MARKET_CONTEXTS.some(c => c.key === detail) ? detail : readStored());
    };
    window.addEventListener(GLOBAL_MARKET_EVENT, onChange);
    return () => window.removeEventListener(GLOBAL_MARKET_EVENT, onChange);
  }, []);

  const update = useCallback((next: GlobalMarketContext) => {
    setValue(next);
    try {
      localStorage.setItem(GLOBAL_MARKET_KEY, next);
    } catch {
      // ignore — worst case the preference just doesn't persist this time
    }
    window.dispatchEvent(new CustomEvent(GLOBAL_MARKET_EVENT, { detail: next }));
  }, []);

  return [value, update];
}
