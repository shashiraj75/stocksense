import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ClosedTradeHorizonBlock } from "@/components/PaperTradeHistoryBlock";
import type { ClosedTradeHorizonBucket, PaperTrade } from "@/utils/api";

// Wave C, WC-N §4 — exactly one "View Postmortem" action for an eligible
// CLOSED trade, gated by the frontend presentation flag, with no new
// Buy/Sell/Close action and no duplicate action after rerender.

const CLOSED_TRADE: PaperTrade = {
  id: 42, symbol: "AAPL", market: "US", quantity: 10, entry_price: 100, exit_price: 108,
  stop_loss: 95, target_price: 110, status: "CLOSED", signal: "test", horizon: "short",
  opened_at: "2026-07-01T10:00:00Z", closed_at: "2026-07-02T10:00:00Z", invested: 1000,
  realized_pnl: 80, trade_management_mode: "manual", exit_reason: "TARGET_HIT",
};

const EMPTY_SUMMARY = {
  closed_trade_count: 1, win_trades_count: 1, win_rate_pct: 100, break_even_count: 0,
  target_hit_count: 1, stop_loss_count: 0, conclusive_count: 1, other_count: 0,
  target_hit_rate_pct: 100, conclusive_rate_pct: 100, net_realized_pnl: 80, avg_realized_return_pct: 8,
};

const BUCKET: ClosedTradeHorizonBucket = {
  summary: EMPTY_SUMMARY, latest_trades: [CLOSED_TRADE], earlier_trade_count: 0,
};

const ORIGINAL_FLAG = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;

beforeEach(() => {
  process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED = "true";
});

afterEach(() => {
  cleanup();
  if (ORIGINAL_FLAG === undefined) delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;
  else process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED = ORIGINAL_FLAG;
});

function renderBlock(bucketExpanded = true) {
  return render(
    <table>
      <tbody>
        <ClosedTradeHorizonBlock
          market="US" horizon="short" label="Short" sub="" accent="text-brand-500"
          bucket={BUCKET} currency="$" blockExpanded={bucketExpanded} onToggleBlock={() => {}}
        />
      </tbody>
    </table>
  );
}

describe("ClosedTradeHorizonBlock — View Postmortem entry point", () => {
  it("renders exactly one View Postmortem link for a closed trade when the flag is enabled", () => {
    renderBlock();
    const links = screen.getAllByText(/view postmortem/i);
    expect(links).toHaveLength(1);
    expect(links[0].closest("a")).toHaveAttribute("href", "/postmortem/42");
  });

  it("renders no View Postmortem action when the frontend flag is disabled", () => {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_PRICE_PATH_ENABLED;
    renderBlock();
    expect(screen.queryByText(/view postmortem/i)).not.toBeInTheDocument();
  });

  it("never introduces a new Buy/Sell/Close control on the closed-trade row", () => {
    renderBlock();
    expect(screen.queryByText(/^buy$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^sell$/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^close$/i)).not.toBeInTheDocument();
  });

  it("does not duplicate the action after a rerender with the same data", () => {
    const { rerender } = renderBlock();
    rerender(
      <table>
        <tbody>
          <ClosedTradeHorizonBlock
            market="US" horizon="short" label="Short" sub="" accent="text-brand-500"
            bucket={BUCKET} currency="$" blockExpanded onToggleBlock={() => {}}
          />
        </tbody>
      </table>
    );
    expect(screen.getAllByText(/view postmortem/i)).toHaveLength(1);
  });
});
