"use client";
import Link from "next/link";
import { Info } from "lucide-react";
import clsx from "clsx";

/**
 * DP-026 — user-facing disclosure (product-owner authorized). Walk-forward
 * validation reuses a single present-day fundamentals snapshot across every
 * historical signal date for a symbol (see backend/services/
 * validation_engine.py's _backtest_stock() comment and DP-026 in
 * DAILY-PICKS-IMPLEMENTATION-REGISTER.md for the full investigation).
 *
 * Deliberately NOT conditioned on the API's `data_limitations` field being
 * present in a given response — every persisted validation run carries this
 * limitation identically, including runs that predate that field, since the
 * underlying fund_score calculation itself (not just its disclosure) is
 * unchanged. Structural: applies to India and US, short/medium/long
 * horizons, and every universe alike — render this unconditionally wherever
 * historical validation/backtest accuracy figures are shown, never gated on
 * a specific API response shape.
 *
 * Two variants:
 *   - DataLimitationsNotice: full banner for dedicated accuracy panels.
 *   - DataLimitationsMark: compact, non-tooltip-only inline marker for tight
 *     spaces (nav badges, per-stock accuracy chips) — reachable by click
 *     (not hover-only) and screen-reader accessible via its own visible
 *     accessible name, not a title attribute alone.
 */
export function DataLimitationsNotice({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        "flex items-start gap-2 rounded-lg border border-blue-500/25 bg-blue-500/[0.06] px-3 py-2",
        className
      )}
    >
      <Info size={14} className="text-blue-300 mt-0.5 shrink-0" aria-hidden="true" />
      <p className="text-[11px] leading-relaxed text-blue-200/90">
        <span className="font-semibold text-blue-200">Historical validation limitation:</span>{" "}
        Fundamentals were not reconstructed as they were known on each past date. A current
        fundamental snapshot was reused across historical signals, so these results are not a
        fully point-in-time replay. Use these results as directional evidence, not as a precise
        reconstruction of past Daily Picks.
      </p>
    </div>
  );
}

export function DataLimitationsMark({ className }: { className?: string }) {
  return (
    <Link
      href="/validation"
      aria-label="Historical validation limitation: a current fundamentals snapshot was reused across historical signals, so this is not a fully point-in-time replay. Open validation page for details."
      title="Not a fully point-in-time replay — see validation limitations"
      className={clsx(
        "inline-flex items-center justify-center text-blue-300/70 hover:text-blue-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-blue-400 rounded-sm",
        className
      )}
    >
      <Info size={11} aria-hidden="true" />
    </Link>
  );
}
