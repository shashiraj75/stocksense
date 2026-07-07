// Pure outcome-label logic for a closed paper trade row (see
// ClosedTradeRow in app/paper-trading/page.tsx). Derives strictly from the
// persisted `exit_reason` field — never from comparing exit_price to
// target_price, and never inferred from realized P&L. See
// frontend/scripts/test-closed-trade-history-paging.mjs's sibling test
// file for direct assertions against this module.

export type ExitReasonLike = "TARGET_HIT" | "STOP_LOSS" | "MANUAL" | string | null | undefined;

export type OutcomeLabel = "Target Hit" | "Stop Loss" | "Manual" | "Non-conclusive";

/**
 * Required mapping:
 *   TARGET_HIT           -> "Target Hit"
 *   STOP_LOSS            -> "Stop Loss"
 *   MANUAL                -> "Manual"
 *   anything else/null/legacy -> "Non-conclusive"
 */
export function outcomeLabel(exitReason: ExitReasonLike): OutcomeLabel {
  if (exitReason === "TARGET_HIT") return "Target Hit";
  if (exitReason === "STOP_LOSS") return "Stop Loss";
  if (exitReason === "MANUAL") return "Manual";
  return "Non-conclusive";
}

/** Break-even count is shown only when it is genuinely greater than zero — never a "Break-even: 0" line. */
export function shouldShowBreakEven(breakEvenCount: number): boolean {
  return breakEvenCount > 0;
}
