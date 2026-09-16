import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ClosedTradeHorizonBlock } from "@/components/PaperTradeHistoryBlock";
import type { ClosedTradeHorizonBucket, PaperTrade } from "@/utils/api";

// 2026-09-16 user request: the "Group by: Month" view showed each month's
// trade count but no P&L/win-rate subtotal for that month, even though the
// block-level header above already shows the equivalent totals for the
// whole bucket. Adds the same subtotal per month group, computed only from
// the trades actually rendered under that group's header (never a separate
// fetch or a different Win Rate definition than the block-level header).

function makeTrade(overrides: Partial<PaperTrade>): PaperTrade {
  return {
    id: 1, symbol: "AAPL", market: "US", quantity: 10, entry_price: 100, exit_price: 108,
    stop_loss: 95, target_price: 110, status: "CLOSED", signal: "test", horizon: "short",
    opened_at: "2026-09-01T10:00:00Z", closed_at: "2026-09-02T10:00:00Z", invested: 1000,
    realized_pnl: 80, trade_management_mode: "manual", exit_reason: "TARGET_HIT",
    ...overrides,
  };
}

// Two Sept trades (one win +80, one loss -30 => net +50, win 1/2) and one
// Aug trade (win +20, win 1/1) — deliberately different per-month math so a
// bug that reused the block-level summary instead of the group's own trades
// would show the wrong numbers for at least one group.
const SEPT_WIN = makeTrade({ id: 1, realized_pnl: 80, exit_reason: "TARGET_HIT", closed_at: "2026-09-02T10:00:00Z" });
const SEPT_LOSS = makeTrade({ id: 2, realized_pnl: -30, exit_reason: "STOP_LOSS", closed_at: "2026-09-05T10:00:00Z" });
const AUG_WIN = makeTrade({ id: 3, realized_pnl: 20, exit_reason: "TARGET_HIT", closed_at: "2026-08-15T10:00:00Z" });

const BUCKET: ClosedTradeHorizonBucket = {
  summary: {
    closed_trade_count: 3, win_trades_count: 2, win_rate_pct: 66.7, break_even_count: 0,
    target_hit_count: 2, stop_loss_count: 1, conclusive_count: 3, other_count: 0,
    target_hit_rate_pct: 66.7, conclusive_rate_pct: 100, net_realized_pnl: 70, avg_realized_return_pct: 5,
  },
  latest_trades: [SEPT_WIN, SEPT_LOSS, AUG_WIN],
  earlier_trade_count: 0,
};

afterEach(cleanup);

function renderBlock() {
  return render(
    <table>
      <tbody>
        <ClosedTradeHorizonBlock
          market="US" horizon="short" label="Short" sub="" accent="text-brand-500"
          bucket={BUCKET} currency="$" blockExpanded onToggleBlock={() => {}}
        />
      </tbody>
    </table>
  );
}

describe("ClosedTradeHorizonBlock — per-month subtotals", () => {
  it("shows each month group's own Win Rate and Net P&L, not the block-level totals", () => {
    renderBlock(); // groupMode defaults to "month"
    expect(screen.getByText(/September 2026/)).toBeInTheDocument();
    expect(screen.getByText(/August 2026/)).toBeInTheDocument();

    // September: 1 win / 2 trades, net = 80 - 30 = +50
    expect(screen.getByText("Win 1/2")).toBeInTheDocument();
    expect(screen.getByText("Net +$50")).toBeInTheDocument();

    // August: 1 win / 1 trade, net = +20 — distinct from September's and
    // from the block-level 66.7%/+$70, proving these are per-group, not a
    // repeated copy of the header summary.
    expect(screen.getByText("Win 1/1")).toBeInTheDocument();
    expect(screen.getByText("Net +$20")).toBeInTheDocument();
  });

  it("shows a losing month's Net P&L with an explicit minus sign, not just red color", () => {
    const loss1 = makeTrade({ id: 10, realized_pnl: -100, exit_reason: "STOP_LOSS", closed_at: "2026-07-01T10:00:00Z" });
    const loss2 = makeTrade({ id: 11, realized_pnl: -50, exit_reason: "STOP_LOSS", closed_at: "2026-07-02T10:00:00Z" });
    const bucket: ClosedTradeHorizonBucket = {
      summary: BUCKET.summary,
      latest_trades: [loss1, loss2],
      earlier_trade_count: 0,
    };
    render(
      <table>
        <tbody>
          <ClosedTradeHorizonBlock
            market="US" horizon="short" label="Short" sub="" accent="text-brand-500"
            bucket={bucket} currency="$" blockExpanded onToggleBlock={() => {}}
          />
        </tbody>
      </table>
    );
    expect(screen.getByText("Win 0/2")).toBeInTheDocument();
    expect(screen.getByText("Net −$150")).toBeInTheDocument();
  });

  it("does not show a month subtotal when grouping is off", () => {
    renderBlock();
    expect(screen.queryByText("Win 1/2")).toBeInTheDocument(); // sanity: month mode is the default
    // Switch to "None" — subtotals are a grouped-view-only concept.
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    expect(screen.queryByText("Win 1/2")).not.toBeInTheDocument();
    expect(screen.queryByText(/September 2026/)).not.toBeInTheDocument();
  });
});
