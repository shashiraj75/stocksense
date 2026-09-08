"use client";
import { useEffect, useState } from "react";
import { getMarketStatus } from "@/utils/marketHours";
import { useGlobalMarketContext } from "@/hooks/useGlobalMarketContext";
import clsx from "clsx";

const MARKETS = [
  { key: "IN" as const,     label: "NSE India",    flag: "🇮🇳" },
  { key: "US" as const,     label: "NYSE / NASDAQ", flag: "🇺🇸" },
  { key: "CRYPTO" as const, label: "Crypto",        flag: "₿"  },
];

type MarketRow = (typeof MARKETS)[number] & { status: ReturnType<typeof getMarketStatus> };

function useMarketStatuses() {
  // Start null so server and client both render nothing on first pass — avoids hydration mismatch
  const [statuses, setStatuses] = useState<MarketRow[] | null>(null);
  useEffect(() => {
    const update = () =>
      setStatuses(MARKETS.map(m => ({ ...m, status: getMarketStatus(m.key) })));
    update();
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, []);
  return statuses;
}

/** Inline version — sits inside the top navbar row.
 * `onlyKey` (added for the single-line header layout, see layout.tsx)
 * restricts rendering to that one market — e.g. only NSE India while the
 * user's selected global context is "IN" — instead of all three. Omitted
 * (or a key MarketStatusBar's MARKETS doesn't have, e.g. "COMMODITY") keeps
 * the original all-markets behavior/renders nothing respectively. */
export function MarketStatusInline({ onlyKey }: { onlyKey?: string } = {}) {
  const statuses = useMarketStatuses();
  if (!statuses) return null;
  const visible = onlyKey ? statuses.filter(s => s.key === onlyKey) : statuses;
  if (!visible.length) return null;
  return (
    <div className="flex items-center flex-wrap gap-x-4 gap-y-1">
      {visible.map(({ key, label, flag, status }) => (
        <div key={key} className="flex flex-col shrink-0">
          <div className="flex items-center gap-1.5">
            <span className="text-sm leading-none">{flag}</span>
            <span className="text-xs text-gray-400">{label}</span>
            <span className="relative flex h-1.5 w-1.5 shrink-0">
              {status.isOpen && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              )}
              <span className={clsx("relative inline-flex rounded-full h-1.5 w-1.5",
                status.isOpen ? "bg-green-500" : "bg-red-500"
              )} />
            </span>
            <span className={clsx("text-xs font-semibold", status.isOpen ? "text-green-400" : "text-red-400")}>
              {status.isOpen ? "Open" : "Closed"}
            </span>
          </div>
          {status.nextEventLabel && (
            <span className="text-[11px] text-gray-300 leading-tight mt-0.5 pl-5">
              {status.nextEventLabel}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** Header status pill scoped to whichever market the user has selected via
 * GlobalMarketDropdown (2026-09-08 user request: "market status should be
 * visible for the selected market only"). Renders nothing for "COMMODITY" —
 * MarketStatusBar has no commodity-market status to show. */
export function SelectedMarketStatusInline() {
  const [context] = useGlobalMarketContext();
  return <MarketStatusInline onlyKey={context} />;
}
