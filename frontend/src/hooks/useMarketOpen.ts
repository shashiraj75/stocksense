"use client";
import { useEffect, useState } from "react";
import { getMarketStatus, type MarketKey } from "@/utils/marketHours";

/**
 * Per-market open/closed status, refreshed every 30s — same cadence as
 * MarketStatusBar's nav-wide indicator. Returns null until the first
 * client-side computation resolves (SSR has no timezone-correct "now"),
 * so callers can distinguish "still unknown" from "known closed" instead
 * of defaulting either way.
 */
export function useMarketOpen(market: MarketKey): boolean | null {
  const [isOpen, setIsOpen] = useState<boolean | null>(null);

  useEffect(() => {
    const update = () => setIsOpen(getMarketStatus(market).isOpen);
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, [market]);

  return isOpen;
}
