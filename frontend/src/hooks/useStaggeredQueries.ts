import { useState, useEffect } from "react";
import { useQueries, UseQueryOptions, UseQueryResult } from "@tanstack/react-query";

/**
 * Like useQueries, but only lets `batchSize` queries run at once instead of
 * firing all of them simultaneously. Needed for pages like Portfolio where
 * the holding count is unbounded — firing one quote + one prediction
 * request per holding (2x the row count) hits the browser's per-origin
 * connection cap (~6) once a portfolio has more than a handful of rows,
 * leaving most rows stuck showing a loading state for a long time even
 * though the backend itself handles the concurrent load fine (verified:
 * 25 concurrent prediction requests completed server-side in ~2.5s total —
 * the bottleneck is the browser's connection queue, not the API).
 */
export function useStaggeredQueries<T>(
  configs: UseQueryOptions<T>[],
  batchSize = 6
): UseQueryResult<T>[] {
  const [unlocked, setUnlocked] = useState(Math.min(batchSize, configs.length));

  const results = useQueries({
    queries: configs.map((c, i) => ({ ...c, enabled: (c.enabled ?? true) && i < unlocked })),
  }) as UseQueryResult<T>[];

  const settledKey = results.slice(0, unlocked).map(r => (r.isLoading ? "0" : "1")).join("");

  useEffect(() => {
    if (unlocked >= configs.length) return;
    const windowSettled = results.slice(0, unlocked).every(r => !r.isLoading);
    if (windowSettled) {
      setUnlocked(u => Math.min(u + batchSize, configs.length));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settledKey, unlocked, configs.length]);

  return results;
}
