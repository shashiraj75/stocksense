"use client";
import { useState, useLayoutEffect, useCallback } from "react";

// Shared across every page with an IN/US (or wider) market toggle — Daily
// Picks, Dashboard, Screener, Backtest, Alerts, Portfolio, Heatmap, Paper
// Trading. Without this, every one of those pages reset to "IN" on every
// refresh/revisit, with no memory of what the user actually wanted to look
// at. One shared localStorage key means picking "US" on one page carries
// over to the others too, not just surviving a reload of the same page.
const KEY = "ss_market_pref";

/**
 * Like useState, but persisted to localStorage under a single shared key.
 * `allowed` scopes which stored values this page will accept — a page that
 * only supports "IN"/"US" will ignore a stored "CRYPTO"/"COMMODITY" value
 * (from a page like Dashboard that supports more) and fall back to
 * `fallback` instead, rather than ending up in an unsupported state.
 */
export function useMarketPreference<T extends string>(
  allowed: readonly T[],
  fallback: T
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(fallback);

  // useLayoutEffect (not useEffect) — fires synchronously before the browser
  // paints, so the stored value swaps in before anything is visible rather
  // than after a frame of `fallback` flashes on screen. No-op during SSR
  // (localStorage isn't available there), which is fine — the very first
  // server-rendered HTML still shows `fallback` for an instant, but the
  // client takes over before paint instead of after.
  useLayoutEffect(() => {
    try {
      const stored = localStorage.getItem(KEY);
      if (stored && (allowed as readonly string[]).includes(stored)) {
        setValue(stored as T);
      }
    } catch {
      // localStorage unavailable (e.g. private browsing) — just keep fallback
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const update = useCallback((next: T) => {
    setValue(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // ignore — worst case the preference just doesn't persist this time
    }
  }, []);

  return [value, update];
}
