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

  const rawResults = useQueries({
    queries: configs.map((c, i) => ({ ...c, enabled: (c.enabled ?? true) && i < unlocked })),
  }) as UseQueryResult<T>[];

  const settledKey = rawResults.slice(0, unlocked).map(r => (r.isLoading ? "0" : "1")).join("");

  useEffect(() => {
    if (unlocked >= configs.length) return;
    const windowSettled = rawResults.slice(0, unlocked).every(r => !r.isLoading);
    if (windowSettled) {
      setUnlocked(u => Math.min(u + batchSize, configs.length));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settledKey, unlocked, configs.length]);

  // A disabled query reports isLoading: false (it never started), which is
  // indistinguishable from "settled with no data" to anything consuming
  // these results — rows still waiting in the queue rendered as "no signal
  // available" instead of "still loading", which looks broken/confusing.
  // Override isLoading for anything not yet unlocked so it reads as
  // pending, since it genuinely is — just queued, not abandoned.
  return rawResults.map((r, i) =>
    i < unlocked ? r : ({ ...r, isLoading: true, isPending: true } as unknown as UseQueryResult<T>)
  );
}
