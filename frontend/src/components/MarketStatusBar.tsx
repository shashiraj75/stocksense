"use client";
import { useEffect, useState } from "react";
import { getMarketStatus } from "@/utils/marketHours";
import clsx from "clsx";

const MARKETS = [
  { key: "IN" as const,     label: "NSE India",    flag: "🇮🇳" },
  { key: "US" as const,     label: "NYSE / NASDAQ", flag: "🇺🇸" },
  { key: "CRYPTO" as const, label: "Crypto",        flag: "₿"  },
];

function useMarketStatuses() {
  const [statuses, setStatuses] = useState(() =>
    MARKETS.map(m => ({ ...m, status: getMarketStatus(m.key) }))
  );
  useEffect(() => {
    const update = () =>
      setStatuses(MARKETS.map(m => ({ ...m, status: getMarketStatus(m.key) })));
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, []);
  return statuses;
}

/** Compact strip for mobile — scrollable single row */
export function MobileMarketStrip() {
  const statuses = useMarketStatuses();
  return (
    <div className="flex items-center gap-4 w-max">
      {statuses.map(({ key, flag, label, status }) => (
        <div key={key} className="flex items-center gap-1 shrink-0">
          <span className="text-xs leading-none">{flag}</span>
          <span className="text-[11px] text-gray-400">{label}</span>
          <span className={clsx("w-1.5 h-1.5 rounded-full shrink-0",
            status.isOpen ? "bg-green-500" : "bg-red-500"
          )} />
          <span className={clsx("text-[11px] font-semibold", status.isOpen ? "text-green-400" : "text-red-400")}>
            {status.isOpen ? "Open" : "Closed"}
          </span>
          {status.nextEventLabel && (
            <span className="text-[10px] text-gray-500 ml-0.5">{status.nextEventLabel}</span>
          )}
        </div>
      ))}
    </div>
  );
}

/** Inline version — sits inside the top navbar row */
export function MarketStatusInline() {
  const statuses = useMarketStatuses();
  return (
    <div className="flex items-center gap-4">
      {statuses.map(({ key, label, flag, status }) => (
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
            <span className="text-[10px] text-gray-400 leading-tight mt-0.5 pl-5">
              {status.nextEventLabel}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
