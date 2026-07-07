// Pure paging/de-duplication logic for a single Trade History horizon
// block's lazy "older closed trades" pagination (see ClosedTradeHorizonBlock
// in app/paper-trading/page.tsx). Extracted from the component so it can be
// exercised directly (see frontend/scripts/test-closed-trade-history-paging.mjs
// — this repo has no frontend test framework, per SES-003/prior sprints'
// established precedent of compiling and asserting against a pure module
// directly rather than introducing one).

export interface MinimalClosedTrade {
  id: number;
  closed_at: string | null;
}

export interface PagingCursor {
  before_closed_at: string;
  before_id: number;
}

/**
 * Derives the cursor for the *first* "older" page request from the last
 * (oldest) row already shown in `latestTrades`. Never returns null when
 * `latestTrades` has a row with a real `closed_at` — starting the first
 * older-history request from a null cursor is exactly the confirmed defect
 * this function exists to prevent: the backend keyset endpoint interprets a
 * null cursor as "start from the top of the bucket," re-returning rows
 * already rendered in `latestTrades`.
 */
export function seedInitialCursor(latestTrades: MinimalClosedTrade[]): PagingCursor | null {
  const last = latestTrades[latestTrades.length - 1];
  if (!last || !last.closed_at) return null;
  return { before_closed_at: last.closed_at, before_id: last.id };
}

/**
 * Filters `incoming` down to trades whose `id` is not already present in
 * `seenIds`, and returns both the fresh trades (in original order) and the
 * updated seen-id set. `seenIds` itself is never mutated in place — callers
 * get a new Set back, matching React's own state-update expectations.
 *
 * `id` is the only comparison key. Two trades with identical symbol, entry
 * price, exit price, and closed_at but different ids are both retained —
 * they are genuinely distinct trades, not duplicates.
 */
export function dedupeAppend<T extends { id: number }>(
  seenIds: ReadonlySet<number>,
  incoming: T[],
): { fresh: T[]; nextSeenIds: Set<number> } {
  const nextSeenIds = new Set(seenIds);
  const fresh: T[] = [];
  for (const trade of incoming) {
    if (nextSeenIds.has(trade.id)) continue;
    nextSeenIds.add(trade.id);
    fresh.push(trade);
  }
  return { fresh, nextSeenIds };
}
