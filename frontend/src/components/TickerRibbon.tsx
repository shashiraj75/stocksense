"use client";
import { useQuery } from "@tanstack/react-query";
import { fetchIndices } from "@/utils/api";
import clsx from "clsx";

/** Combines IN + US + CRYPTO indices into one continuously-scrolling
 * marquee ribbon, replacing three separately-scrollable static rows.
 * The item list is duplicated back-to-back and the whole strip is
 * translated exactly one copy-width — once the animation loops back to
 * 0%, the (identical) second copy is already sitting where the first
 * one started, so the seam is invisible. Pauses on hover/focus so a
 * user can actually read a value without it sliding away, and is
 * skipped entirely under prefers-reduced-motion (see globals.css). */
export function TickerRibbon() {
  const inQuery = useQuery({
    queryKey: ["indices", "IN"],
    queryFn: () => fetchIndices("IN"),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
  const usQuery = useQuery({
    queryKey: ["indices", "US"],
    queryFn: () => fetchIndices("US"),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
  const cryptoQuery = useQuery({
    queryKey: ["indices", "CRYPTO"],
    queryFn: () => fetchIndices("CRYPTO"),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });

  const allIndices = [
    ...(inQuery.data?.indices ?? []),
    ...(usQuery.data?.indices ?? []),
    ...(cryptoQuery.data?.indices ?? []),
  ].filter((idx) => idx.price != null);

  if (allIndices.length === 0) return null;

  // ~4.5s per item reads comfortably at a walking pace regardless of how
  // many indices are configured — a longer list scrolls proportionally
  // slower rather than rushing past.
  const duration = Math.max(allIndices.length * 4.5, 18);

  const renderItems = (copyKey: string) =>
    allIndices.map((idx) => {
      const up = (idx.change_pct ?? 0) >= 0;
      return (
        <div key={`${copyKey}-${idx.symbol}`} className="flex items-center gap-2 shrink-0 px-4">
          <span className="text-gray-400 text-xs">{idx.name}</span>
          <span className="font-mono font-bold text-white text-xs">
            {idx.price!.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          {idx.change_pct !== null && (
            <span className={clsx("text-xs font-medium", up ? "text-bull" : "text-bear")}>
              {up ? "▲" : "▼"}{" "}
              {idx.change_pts !== null && idx.change_pts !== undefined
                ? `${Math.abs(idx.change_pts).toLocaleString(undefined, { maximumFractionDigits: 2 })} (${Math.abs(idx.change_pct).toFixed(2)}%)`
                : `${Math.abs(idx.change_pct).toFixed(2)}%`}
            </span>
          )}
          <span className="text-dark-border text-xs">|</span>
        </div>
      );
    });

  return (
    <div className="overflow-hidden group" aria-label="Live market index ticker">
      <div
        className="flex w-max animate-ticker group-hover:[animation-play-state:paused] group-focus-within:[animation-play-state:paused]"
        style={{ "--ticker-duration": `${duration}s` } as React.CSSProperties}
      >
        {/* Two identical copies back-to-back — see the component docblock. */}
        <div className="flex shrink-0">{renderItems("a")}</div>
        <div className="flex shrink-0" aria-hidden="true">{renderItems("b")}</div>
      </div>
    </div>
  );
}
