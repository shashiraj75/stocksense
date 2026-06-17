"use client";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchIndices, Market } from "@/utils/api";
import { getMarketStatus } from "@/utils/marketHours";
import clsx from "clsx";

export function IndexBar({ market }: { market: Market | "CRYPTO" }) {
  const { data, isFetching } = useQuery({
    queryKey: ["indices", market],
    queryFn: () => fetchIndices(market),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const [status, setStatus] = useState(() => getMarketStatus(market));
  useEffect(() => {
    const update = () => setStatus(getMarketStatus(market));
    update();
    const id = setInterval(update, 30_000); // recheck every 30s — cheap, catches open/close transitions promptly
    return () => clearInterval(id);
  }, [market]);

  if (!data?.indices?.length) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 px-1 py-2 text-sm">
      <div className="flex items-center gap-4 overflow-x-auto scrollbar-hide pb-0.5">
        {data.indices.map((idx) => {
          const up = (idx.change_pct ?? 0) >= 0;
          if (!idx.price) return null;
          return (
            <div key={idx.symbol} className="flex items-center gap-2 shrink-0">
              <span className="text-gray-400 text-xs">{idx.name}</span>
              <span className="font-mono font-bold text-white">
                {idx.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
              </span>
              {idx.change_pct !== null && (
                <span className={clsx("text-xs font-medium", up ? "text-bull" : "text-bear")}>
                  {up ? "▲" : "▼"} {Math.abs(idx.change_pct).toFixed(2)}%
                </span>
              )}
            </div>
          );
        })}
      </div>
      {/* Market open/closed indicator */}
      <div className="flex items-center gap-1.5 text-xs text-gray-500 shrink-0">
        <span
          className={clsx(
            "w-1.5 h-1.5 rounded-full",
            status.isOpen ? "bg-green-500 animate-pulse" : "bg-red-500"
          )}
        />
        <span>
          {isFetching ? "Updating…" : status.isOpen ? "Live" : status.label}
          {!isFetching && status.nextEventLabel && (
            <span className="text-gray-600"> · {status.nextEventLabel}</span>
          )}
        </span>
      </div>
    </div>
  );
}
