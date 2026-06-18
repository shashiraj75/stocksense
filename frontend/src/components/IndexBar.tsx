"use client";
import { useQuery } from "@tanstack/react-query";
import { fetchIndices, Market } from "@/utils/api";
import clsx from "clsx";

export function IndexBar({ market, inline }: { market: Market | "CRYPTO"; inline?: boolean }) {
  const { data, isFetching } = useQuery({
    queryKey: ["indices", market],
    queryFn: () => fetchIndices(market),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  if (!data?.indices?.length) return null;

  const items = (
    <>
      {data.indices.map((idx) => {
        const up = (idx.change_pct ?? 0) >= 0;
        if (!idx.price) return null;
        return (
          <div key={idx.symbol} className="flex items-center gap-2 shrink-0">
            <span className="text-gray-400 text-xs">{idx.name}</span>
            <span className="font-mono font-bold text-white text-xs">
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
      {isFetching && <span className="text-xs text-gray-600 shrink-0">Updating…</span>}
    </>
  );

  if (inline) {
    return <div className="flex items-center gap-4 w-max">{items}</div>;
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-4 px-1 py-2 text-sm">
      <div className="flex items-center gap-4 overflow-x-auto scrollbar-hide pb-0.5">{items}</div>
    </div>
  );
}
