"use client";
import React, { useState, useRef, useMemo } from "react";
import Link from "next/link";
import clsx from "clsx";
import { ExternalLink, Check, X, ChevronDown, ChevronUp } from "lucide-react";
import {
  fetchOlderClosedTrades, type PaperTrade, type ClosedTradeHorizonBucket,
  type ClosedHistoryHorizonKey, type OlderClosedTradesCursor,
} from "@/utils/api";
import { seedInitialCursor, dedupeAppend } from "@/utils/closedTradeHistoryPaging";
import { runIfNotInFlight } from "@/utils/inFlightGuard";
import { outcomeLabel, shouldShowBreakEven } from "@/utils/paperTradeOutcome";
import { isTradePostmortemPricePathEnabled } from "@/utils/featureFlags";
import { groupClosedTradesByMonth, groupClosedTradesByYear } from "@/utils/groupTradesByPeriod";

type GroupMode = "none" | "month" | "year";

const fmt = (n: number, dec = 2, locale = "en-IN") =>
  n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });

function ClosedTradeRow({ trade }: { trade: PaperTrade }) {
  const currency = trade.market === "IN" ? "₹" : "$";
  const locale = trade.market === "IN" ? "en-IN" : "en-US";
  const row = (n: number, dec = 2) => n.toLocaleString(locale, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const pnl = trade.realized_pnl ?? 0;
  const pnlPct = trade.entry_price > 0
    ? ((trade.exit_price! - trade.entry_price) / trade.entry_price * 100)
    : 0;
  return (
    <tr className="border-b border-dark-border last:border-0 hover:bg-white/[0.02] transition-colors">
      <td className="px-4 py-3">
        <Link href={`/stock/${trade.symbol}?market=${trade.market}`}
          className="font-bold text-white hover:text-brand-400 flex items-center gap-1">
          {trade.symbol} <ExternalLink size={11} className="opacity-50" />
        </Link>
        <p className="text-xs text-gray-500">{trade.market} · {trade.horizon}</p>
      </td>
      <td className="px-4 py-3 text-sm font-mono">{trade.quantity}</td>
      <td className="px-4 py-3 text-sm font-mono">{currency}{row(trade.entry_price)}</td>
      <td className="px-4 py-3 text-sm font-mono">{currency}{row(trade.exit_price ?? 0)}</td>
      <td className="px-4 py-3">
        <span className={clsx("text-sm font-bold font-mono", pnl >= 0 ? "text-bull" : "text-bear")}>
          {pnl >= 0 ? "+" : ""}{currency}{row(Math.abs(pnl))}
          <span className="text-xs font-normal ml-1 opacity-80">
            ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%)
          </span>
        </span>
      </td>
      <td className="px-4 py-3">
        {/* Outcome label is derived strictly from the authoritative
            exit_reason field — never from comparing exit_price to
            target_price. A trade can close above its target via a manual
            sell (not a genuine target-hit) or close via a stop-loss trigger
            at a price that happens to be >= target (a stale/edited target),
            so a price comparison alone can misreport the true outcome. */}
        {trade.exit_reason === "TARGET_HIT" ? (
          <span className="flex items-center gap-1 text-xs text-bull font-medium">
            <Check size={12} /> {outcomeLabel(trade.exit_reason)}
          </span>
        ) : trade.exit_reason === "STOP_LOSS" ? (
          <span className="flex items-center gap-1 text-xs text-bear font-medium">
            <X size={12} /> {outcomeLabel(trade.exit_reason)}
          </span>
        ) : trade.exit_reason === "MANUAL" ? (
          <span className="text-xs text-gray-500">{outcomeLabel(trade.exit_reason)}</span>
        ) : (
          // NULL, legacy, expiry, cancellation, stale, or any other value —
          // explicitly labelled rather than a bare dash, so it reads as a
          // deliberate non-conclusive classification, not missing data.
          <span className="text-xs text-gray-600">{outcomeLabel(trade.exit_reason)}</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {trade.closed_at ? new Date(trade.closed_at).toLocaleDateString("en-IN") : "—"}
      </td>
      <td className="px-4 py-3">
        {isTradePostmortemPricePathEnabled() && (
          // Backend capability (TRADE_POSTMORTEM_PRICE_PATH_ENABLED) remains
          // authoritative regardless of this frontend gate — the destination
          // page itself renders FEATURE_DISABLED explicitly when the backend
          // flag is off, so this link is never a broken promise, only a
          // presentation choice about whether to show the entry point at all.
          // No open trade ever reaches this row (ClosedTradeRow only), and
          // this Link renders exactly once per row — no rerender-triggered
          // duplicate action is possible since it's plain navigation, not a
          // mutation.
          <Link
            href={`/postmortem/${trade.id}`}
            className="text-xs text-gray-400 hover:text-brand-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded whitespace-nowrap"
          >
            View Postmortem
          </Link>
        )}
      </td>
    </tr>
  );
}

// One horizon's Trade History block. `bucket` is rendered verbatim from
// GET /portfolio's closed_trade_history_by_horizon — this component never
// sorts, groups, classifies, or slices a full closed-trade list; it only
// (a) shows the backend-computed summary/latest_trades/earlier_trade_count,
// and (b) lazily fetches additional pages of *that exact horizon's* older
// trades via fetchOlderClosedTrades only once the user expands the
// "Show N earlier" control — never prefetched, never re-fetched on a
// second expand (already-loaded pages stay in local state).
//
// Extracted out of paper-trading/page.tsx (and loaded via next/dynamic
// there) purely to shrink that route's initial JS payload — Paper Trading
// was by far the largest client page in the app (1483 lines, ~2x
// Portfolio), and this below-the-fold history section was the single
// largest self-contained chunk of it. No behavior change from the inline
// version.
export function ClosedTradeHorizonBlock({
  market, horizon, label, sub, accent, bucket, currency, blockExpanded, onToggleBlock,
}: {
  market: PaperTrade["market"]; horizon: ClosedHistoryHorizonKey;
  label: string; sub: string; accent: string;
  bucket: ClosedTradeHorizonBucket; currency: string;
  blockExpanded: boolean; onToggleBlock: () => void;
}) {
  const { summary, latest_trades, earlier_trade_count } = bucket;
  const netPnl = summary.net_realized_pnl;

  // Seed the very first "older" page's cursor from the last (oldest) row
  // already shown in latest_trades — never start from a null cursor when
  // initial rows exist, or the older-history endpoint restarts from the top
  // of the bucket and returns rows already rendered (the confirmed
  // duplicate-row defect, e.g. the observed duplicated LLY trade). Lazy
  // useState initializer so this only runs once, from the props this
  // component mounted with. See seedInitialCursor's own unit coverage in
  // frontend/scripts/test-closed-trade-history-paging.mjs.
  const [cursor, setCursor] = useState<OlderClosedTradesCursor | null>(() => seedInitialCursor(latest_trades));
  const [olderTrades, setOlderTrades] = useState<PaperTrade[]>([]);
  const [hasMore, setHasMore] = useState(earlier_trade_count > 0);
  const [olderVisible, setOlderVisible] = useState(false);
  const [olderLoaded, setOlderLoaded] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  // Month/Year grouping is purely a display concern over rows already
  // fetched/paginated above — it never affects what's fetched, only how
  // the already-rendered rows are sectioned. Defaults to "month" (the
  // most commonly useful grouping for reviewing recent activity).
  const [groupMode, setGroupMode] = useState<GroupMode>("month");

  // The sole de-duplication key across every render source in this block
  // (latest_trades + any already-fetched older pages) — trade_id only.
  // Never symbol/timestamp/price/visible text: two genuinely distinct
  // trades can legitimately share all of those. See dedupeAppend's own
  // unit coverage in frontend/scripts/test-closed-trade-history-paging.mjs.
  // This is a defense-in-depth safeguard against duplicate *rows* — it
  // must not be relied on to mask duplicate *requests*; see
  // olderHistoryRequestInFlightRef below for that.
  const seenTradeIdsRef = useRef<Set<number>>(new Set(latest_trades.map(t => t.id)));

  // Synchronous in-flight lock, separate from `loadingOlder` React state.
  // `loadingOlder` exists for visible loading feedback/accessibility
  // (`disabled={loadingOlder}` below) but React state updates are batched,
  // so a second `loadNextPage()` invocation in the same synchronous tick
  // (a rapid double-click, or a click racing a keyboard activation) can
  // still read a stale `loadingOlder === false` from its own closure before
  // the first call's `setLoadingOlder(true)` has committed. A ref mutation
  // is immediate and shared across both invocations, so this guard cannot
  // be bypassed the same way.
  const olderHistoryRequestInFlightRef = useRef(false);

  const loadNextPage = async () => {
    // runIfNotInFlight checks-and-sets olderHistoryRequestInFlightRef.current
    // synchronously, before any await, so a second invocation arriving in
    // the same tick (a rapid double-click, or a click racing a keyboard
    // activation) sees the ref already true and is rejected immediately —
    // it never issues a second network request. The ref is always reset in
    // a `finally`, including on a thrown/rejected fetch, so a failed
    // request never permanently locks this horizon's pagination.
    // `loadingOlder` React state remains the visible loading indicator and
    // keeps the button's `disabled={loadingOlder}` for accessibility — it
    // is a secondary, UI-facing signal, not the concurrency guard itself.
    await runIfNotInFlight(olderHistoryRequestInFlightRef, async () => {
      setLoadingOlder(true);
      try {
        const res = await fetchOlderClosedTrades(market, horizon, cursor);
        // Defensive de-duplication: even if the backend response overlapped
        // with something already rendered, a trade_id already seen anywhere
        // in this block is dropped before appending — distinct trade_ids
        // with identical symbol/price/date are never treated as duplicates.
        const { fresh, nextSeenIds } = dedupeAppend(seenTradeIdsRef.current, res.trades);
        seenTradeIdsRef.current = nextSeenIds;
        setOlderTrades(prev => [...prev, ...fresh]);
        setCursor(res.next_cursor);
        setHasMore(res.has_more);
        setOlderLoaded(true);
        setOlderVisible(true);
      } finally {
        setLoadingOlder(false);
      }
    });
  };

  const remainingCount = earlier_trade_count - olderTrades.length;

  // Grouping is a pure display transform over exactly the rows already
  // rendered without grouping (latest_trades + visible older pages) — it
  // never fetches, sorts across pages differently, or affects pagination
  // state. Recomputed only when the underlying visible rows or the
  // selected mode change.
  const visibleTrades = useMemo(
    () => [...latest_trades, ...(olderVisible ? olderTrades : [])],
    [latest_trades, olderVisible, olderTrades],
  );
  const groupedTrades = useMemo(() => {
    if (groupMode === "month") return groupClosedTradesByMonth(visibleTrades);
    if (groupMode === "year") return groupClosedTradesByYear(visibleTrades);
    return [];
  }, [groupMode, visibleTrades]);

  return (
    <div className={clsx("bg-dark-card border border-dark-border rounded-xl overflow-hidden border-l-4", accent)}>
      <button
        onClick={onToggleBlock}
        aria-expanded={blockExpanded}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          {blockExpanded ? <ChevronUp size={15} className="text-gray-500 shrink-0" /> : <ChevronDown size={15} className="text-gray-500 shrink-0" />}
          <span className="font-semibold text-sm text-white">{label}</span>
          <span className="text-xs text-gray-500">({sub})</span>
        </div>
        <div className="flex items-center gap-3 flex-wrap text-xs text-gray-400 justify-end">
          <span>{summary.closed_trade_count} closed</span>
          <span>
            Win Rate{" "}
            {summary.win_rate_pct !== null
              ? <span className="text-gray-300 font-medium">{summary.win_trades_count} / {summary.closed_trade_count} · {summary.win_rate_pct.toFixed(1)}%</span>
              : <span className="text-gray-600">—</span>}
          </span>
          <span className={clsx("font-mono font-bold", netPnl > 0 ? "text-bull" : netPnl < 0 ? "text-bear" : "text-gray-400")}>
            Net P&L {netPnl >= 0 ? "+" : ""}{currency}{fmt(Math.abs(netPnl), 0)}
          </span>
        </div>
      </button>

      {blockExpanded && (
        <div className="border-t border-dark-border">
          {/* Target Hit Rate is a separate, secondary metric from Win Rate
              above — it only ever concerns conclusive (TARGET_HIT/STOP_LOSS)
              outcomes, never total closed trades or P&L sign. Never merge
              the two numbers. */}
          <div className="px-4 py-2 flex items-center gap-4 flex-wrap text-[11px] text-gray-500 border-b border-dark-border/60">
            <span>
              Target Hit Rate:{" "}
              {summary.target_hit_rate_pct !== null
                ? <span className="text-gray-400">{summary.target_hit_count} / {summary.conclusive_count} · {summary.target_hit_rate_pct.toFixed(1)}%</span>
                : <span className="text-gray-600">no conclusive outcomes</span>}
            </span>
            <span>
              Conclusive outcomes:{" "}
              <span className="text-gray-400">
                {summary.conclusive_count} / {summary.closed_trade_count}
                {summary.conclusive_rate_pct !== null && ` · ${summary.conclusive_rate_pct.toFixed(1)}%`}
              </span>
            </span>
            <span>Stop-loss outcomes: <span className="text-gray-400">{summary.stop_loss_count} / {summary.conclusive_count || 0}</span></span>
            <span>Other / non-conclusive: <span className="text-gray-400">{summary.other_count}</span></span>
            {/* Shown only when it exists — never a "Break-even: 0" line.
                Win Rate logic itself is unchanged: break-even trades never
                count as wins, only appear here as an informational count. */}
            {shouldShowBreakEven(summary.break_even_count) && (
              <span>Break-even: <span className="text-gray-400">{summary.break_even_count}</span></span>
            )}
            <span>
              Avg realized return:{" "}
              <span className="text-gray-400">
                {summary.avg_realized_return_pct !== null
                  ? `${summary.avg_realized_return_pct >= 0 ? "+" : ""}${summary.avg_realized_return_pct.toFixed(2)}%`
                  : "—"}
              </span>
            </span>
          </div>
          <div className="px-4 py-2 flex items-center gap-2 text-[11px] text-gray-500 border-b border-dark-border/60">
            <span>Group by:</span>
            {(["none", "month", "year"] as const).map(mode => (
              <button
                key={mode}
                onClick={() => setGroupMode(mode)}
                aria-pressed={groupMode === mode}
                className={clsx(
                  "px-2 py-0.5 rounded-full border transition-colors capitalize",
                  groupMode === mode
                    ? "border-brand-500 text-brand-400 bg-brand-500/10"
                    : "border-dark-border text-gray-500 hover:text-gray-300",
                )}
              >
                {mode === "none" ? "None" : mode}
              </button>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-dark-border text-xs text-gray-500">
                  <th className="px-4 py-2.5 text-left">Stock</th>
                  <th className="px-4 py-2.5 text-left">Qty</th>
                  <th className="px-4 py-2.5 text-left">Entry</th>
                  <th className="px-4 py-2.5 text-left">Exit</th>
                  <th className="px-4 py-2.5 text-left">P&L</th>
                  <th className="px-4 py-2.5 text-left">Outcome</th>
                  <th className="px-4 py-2.5 text-left">Closed</th>
                  {isTradePostmortemPricePathEnabled() && <th className="px-4 py-2.5 text-left" />}
                </tr>
              </thead>
              <tbody>
                {groupMode === "none" ? (
                  <>
                    {latest_trades.map(t => <ClosedTradeRow key={t.id} trade={t} />)}
                    {olderVisible && olderTrades.map(t => <ClosedTradeRow key={t.id} trade={t} />)}
                  </>
                ) : (
                  groupedTrades.map(group => (
                    <React.Fragment key={group.key}>
                      <tr className="bg-white/[0.03]">
                        <td
                          colSpan={isTradePostmortemPricePathEnabled() ? 8 : 7}
                          className="px-4 py-1.5 text-[11px] font-semibold text-gray-400 uppercase tracking-wide"
                        >
                          {group.label}
                          <span className="ml-2 font-normal normal-case text-gray-600">
                            ({group.trades.length} trade{group.trades.length === 1 ? "" : "s"})
                          </span>
                        </td>
                      </tr>
                      {group.trades.map(t => <ClosedTradeRow key={t.id} trade={t} />)}
                    </React.Fragment>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {earlier_trade_count > 0 && (
            <button
              onClick={() => {
                if (!olderLoaded) { loadNextPage(); return; }       // first expand — fetch page 1
                if (!olderVisible) { setOlderVisible(true); return; } // re-show already-fetched rows, no re-fetch
                if (hasMore) { loadNextPage(); return; }              // fetch the next page, append
                setOlderVisible(false);                               // fully exhausted — collapse
              }}
              disabled={loadingOlder}
              aria-expanded={olderVisible}
              className="w-full flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs text-gray-400 hover:text-white border-t border-dark-border transition-colors disabled:opacity-50"
            >
              {loadingOlder
                ? "Loading…"
                : !olderLoaded
                  ? <>Show {earlier_trade_count} earlier closed trade{earlier_trade_count === 1 ? "" : "s"} <ChevronDown size={13} /></>
                  : !olderVisible
                    ? <>Show {olderTrades.length} earlier closed trade{olderTrades.length === 1 ? "" : "s"} <ChevronDown size={13} /></>
                    : hasMore
                      ? <>Show {remainingCount} more earlier closed trade{remainingCount === 1 ? "" : "s"} <ChevronDown size={13} /></>
                      : <>Hide earlier trades <ChevronUp size={13} /></>}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default ClosedTradeHorizonBlock;
