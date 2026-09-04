// Pure grouping logic for the Trade History table's Month/Year sections
// (see ClosedTradeHorizonBlock in components/PaperTradeHistoryBlock.tsx).
// Groups an already-ordered (newest-first, by closed_at) list of closed
// trades into Month/Year buckets, preserving the input's own relative
// order within and across groups — this function never sorts, it only
// partitions, so it stays correct regardless of how the backend/paging
// layer orders its input.

export interface MinimalGroupableTrade {
  id: number;
  closed_at: string | null;
}

export interface TradeGroup<T extends MinimalGroupableTrade> {
  /** Stable grouping key, e.g. "2026-09" for September 2026, or "unknown" for a null closed_at. */
  key: string;
  /** Display label, e.g. "September 2026", or "Date unavailable" for a null closed_at. */
  label: string;
  trades: T[];
}

const MONTH_LABELS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/**
 * Groups `trades` by the calendar month/year of `closed_at`, in UTC (the
 * same instant every trade's `closed_at` timestamp already represents —
 * this never re-derives a market-local calendar day/month, which could
 * legitimately place the same instant in a different month than UTC for
 * trades closing very close to midnight; the grouping only needs a stable,
 * consistent bucket, not a market-accurate calendar boundary). A null or
 * unparseable `closed_at` is grouped into a single trailing "Date
 * unavailable" bucket rather than dropped or crashing.
 *
 * Never reorders `trades` — a group's own `trades` array is a filtered
 * subsequence of the input in the input's original order, and groups
 * themselves appear in first-encountered order (which is always
 * newest-first when fed the already newest-first closed-trade lists this
 * app uses throughout).
 */
export function groupClosedTradesByMonth<T extends MinimalGroupableTrade>(
  trades: T[],
): TradeGroup<T>[] {
  const groups: TradeGroup<T>[] = [];
  const indexByKey = new Map<string, number>();
  const UNKNOWN_KEY = "unknown";

  for (const trade of trades) {
    let key = UNKNOWN_KEY;
    let label = "Date unavailable";
    if (trade.closed_at) {
      const d = new Date(trade.closed_at);
      if (!Number.isNaN(d.getTime())) {
        const year = d.getUTCFullYear();
        const month = d.getUTCMonth(); // 0-11
        key = `${year}-${String(month + 1).padStart(2, "0")}`;
        label = `${MONTH_LABELS[month]} ${year}`;
      }
    }

    let idx = indexByKey.get(key);
    if (idx === undefined) {
      idx = groups.length;
      indexByKey.set(key, idx);
      groups.push({ key, label, trades: [] });
    }
    groups[idx].trades.push(trade);
  }

  return groups;
}

/**
 * Groups `trades` by calendar year of `closed_at` (UTC) — same contract as
 * groupClosedTradesByMonth, coarser granularity. Used by the Year toggle.
 */
export function groupClosedTradesByYear<T extends MinimalGroupableTrade>(
  trades: T[],
): TradeGroup<T>[] {
  const groups: TradeGroup<T>[] = [];
  const indexByKey = new Map<string, number>();
  const UNKNOWN_KEY = "unknown";

  for (const trade of trades) {
    let key = UNKNOWN_KEY;
    let label = "Date unavailable";
    if (trade.closed_at) {
      const d = new Date(trade.closed_at);
      if (!Number.isNaN(d.getTime())) {
        const year = d.getUTCFullYear();
        key = String(year);
        label = String(year);
      }
    }

    let idx = indexByKey.get(key);
    if (idx === undefined) {
      idx = groups.length;
      indexByKey.set(key, idx);
      groups.push({ key, label, trades: [] });
    }
    groups[idx].trades.push(trade);
  }

  return groups;
}
