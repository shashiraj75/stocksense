"use client";
import { useState, useLayoutEffect, useEffect, useCallback } from "react";
import { GLOBAL_MARKET_KEY, GLOBAL_MARKET_EVENT } from "./useGlobalMarketContext";

// Shared across every page with an IN/US (or wider) market toggle — Daily
// Picks, Dashboard, Screener, Backtest, Alerts, Portfolio, Heatmap, Paper
// Trading — and with the header's global GlobalMarketDropdown. Without this,
// every one of those pages reset to "IN" on every refresh/revisit, with no
// memory of what the user actually wanted to look at. One shared
// localStorage key means picking "US" on one page carries over to the
// others too, not just surviving a reload of the same page.
const KEY = GLOBAL_MARKET_KEY;

/**
 * Like useState, but persisted to localStorage under a single shared key.
 * `allowed` scopes which stored values this page will accept — a page that
 * only supports "IN"/"US" will ignore a stored "CRYPTO"/"COMMODITY" value
 * (from a page like Dashboard that supports more) and fall back to
 * `fallback` instead, rather than ending up in an unsupported state.
 *
 * Also stays live-synced across every other mounted instance of this hook
 * (including the header's global dropdown) via a custom window event — the
 * native `storage` event never fires in the same tab that made the change,
 * so without this, changing the market elsewhere on the same page load
 * wouldn't be reflected here until a full reload.
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

  // Live cross-instance sync. Only adopts values this page's own `allowed`
  // list supports — an unsupported global selection (e.g. CRYPTO on an
  // IN/US-only page) is silently ignored here, exactly like the initial
  // read above. Pages that want to show a "coming soon" notice for an
  // unsupported global selection should pair this with
  // useGlobalMarketContext's raw value instead of relying on this hook alone.
  useEffect(() => {
    const onChange = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail;
      if (detail && (allowed as readonly string[]).includes(detail)) {
        setValue(detail as T);
      }
    };
    window.addEventListener(GLOBAL_MARKET_EVENT, onChange);
    return () => window.removeEventListener(GLOBAL_MARKET_EVENT, onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed.join(",")]);

  const update = useCallback((next: T) => {
    setValue(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      // ignore — worst case the preference just doesn't persist this time
    }
    window.dispatchEvent(new CustomEvent(GLOBAL_MARKET_EVENT, { detail: next }));
  }, []);

  return [value, update];
}
