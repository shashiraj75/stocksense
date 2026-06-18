"use client";
import { useEffect, useState } from "react";
import { getMarketStatus } from "@/utils/marketHours";
import { LiveClock } from "@/components/LiveClock";
import clsx from "clsx";

const MARKETS = [
  { key: "IN" as const,     label: "NSE India",    flag: "🇮🇳" },
  { key: "US" as const,     label: "NYSE / NASDAQ", flag: "🇺🇸" },
  { key: "CRYPTO" as const, label: "Crypto",        flag: "₿"  },
];

export function MarketStatusBar() {
  const [statuses, setStatuses] = useState(() =>
    MARKETS.map(m => ({ ...m, status: getMarketStatus(m.key) }))
  );

  useEffect(() => {
    const update = () =>
      setStatuses(MARKETS.map(m => ({ ...m, status: getMarketStatus(m.key) })));
    const id = setInterval(update, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="border-t border-dark-border/40 bg-dark-bg/60">
      <div className="max-w-7xl mx-auto px-4 py-1.5 flex flex-nowrap items-center gap-5 overflow-x-auto whitespace-nowrap">
        <LiveClock inline />
        <span className="text-gray-700 text-xs shrink-0">|</span>
        {statuses.map(({ key, label, flag, status }) => (
          <div key={key} className="flex items-center gap-1.5 shrink-0">
            <span className="text-sm leading-none">{flag}</span>
            <span className="text-xs text-gray-500">{label}</span>
            <span className="relative flex h-1.5 w-1.5 shrink-0">
              {status.isOpen && (
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              )}
              <span className={clsx(
                "relative inline-flex rounded-full h-1.5 w-1.5",
                status.isOpen ? "bg-green-500" : "bg-red-500"
              )} />
            </span>
            <span className={clsx("text-xs font-medium", status.isOpen ? "text-green-400" : "text-red-400")}>
              {status.isOpen ? "Open" : "Closed"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
