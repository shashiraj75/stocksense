"use client";
import { useQuery } from "@tanstack/react-query";
import { fetchIndices, Market } from "@/utils/api";
import clsx from "clsx";

export function IndexBar({ market }: { market: Market | "CRYPTO" }) {
  const { data, isFetching } = useQuery({
    queryKey: ["indices", market],
    queryFn: () => fetchIndices(market),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  if (!data?.indices?.length) return null;

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 px-1 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-5">
        {data.indices.map((idx) => {
          const up = (idx.change_pct ?? 0) >= 0;
          if (!idx.price) return null;
          return (
            <div key={idx.symbol} className="flex items-center gap-2">
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
      {/* Live pulse indicator */}
      <div className="flex items-center gap-1.5 text-xs text-gray-500 shrink-0">
        <span className={clsx("w-1.5 h-1.5 rounded-full", isFetching ? "bg-yellow-400 animate-pulse" : "bg-green-500 animate-pulse")} />
        <span>{isFetching ? "Updating…" : "Live · 15s"}</span>
      </div>
    </div>
  );
}
