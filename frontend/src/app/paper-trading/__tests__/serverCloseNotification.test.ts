import { describe, it, expect } from "vitest";
import { findClosedTradeById, buildServerCloseMessage } from "../page";
import type { PaperPortfolio, PaperTrade, ClosedTradeHistoryByMarket } from "@/utils/api";

// Regression coverage for the on-screen "close banner" this tab shows when
// a trade closes WITHOUT this tab having executed the close itself — the
// common case since services/paper_trade_exit_monitor.py started closing
// Auto Close trades server-side every 5 minutes. Before this fix, the
// banner only ever fired from OpenTradeRow's own closeMutation, so a trade
// closed by the backend monitor produced an email but no on-screen banner
// (2026-09-15 user report: "we used to get notifications on top of the
// screen").

function makeTrade(overrides: Partial<PaperTrade> = {}): PaperTrade {
  return {
    id: 1,
    symbol: "AAPL",
    market: "US",
    quantity: 3,
    entry_price: 226.71,
    exit_price: 211.3,
    stop_loss: 211.3,
    target_price: 250,
    status: "CLOSED",
    signal: "BUY",
    horizon: "short",
    opened_at: "2026-09-10T00:00:00Z",
    closed_at: "2026-09-15T00:00:00Z",
    invested: 680.13,
    realized_pnl: -46.23,
    trade_management_mode: "auto",
    exit_reason: "STOP_LOSS",
    ...overrides,
  };
}

function emptyBucketMarket(): ClosedTradeHistoryByMarket {
  const emptySummary = {
    closed_trade_count: 0, win_trades_count: 0, win_rate_pct: null, break_even_count: 0,
    target_hit_count: 0, stop_loss_count: 0, conclusive_count: 0, other_count: 0,
    target_hit_rate_pct: null, conclusive_rate_pct: null, net_realized_pnl: 0, avg_realized_return_pct: null,
  };
  const emptyBucket = { summary: emptySummary, latest_trades: [], earlier_trade_count: 0 };
  return { short: { ...emptyBucket }, medium: { ...emptyBucket }, long: { ...emptyBucket } };
}

function makePortfolio(closedTrade: PaperTrade | null): PaperPortfolio {
  const IN = emptyBucketMarket();
  const US = emptyBucketMarket();
  if (closedTrade) {
    const bucket = closedTrade.market === "IN" ? IN : US;
    bucket[closedTrade.horizon as "short"].latest_trades = [closedTrade];
  }
  return {
    user_id: "u1", cash: 0, cash_usd: 0, starting_cash: 100000, starting_cash_usd: 100000,
    open_trades: [],
    total_realized_pnl: 0, total_realized_pnl_usd: 0,
    closed_trade_summary: {} as any,
    closed_trade_history_by_horizon: { IN, US },
    closed_trade_overview_by_market: {} as any,
    email_notifications_enabled: true,
  };
}

describe("findClosedTradeById", () => {
  it("finds a trade in the market/horizon bucket it actually closed under", () => {
    const trade = makeTrade({ id: 42, market: "US", horizon: "short" });
    const portfolio = makePortfolio(trade);
    expect(findClosedTradeById(portfolio, 42)).toEqual(trade);
  });

  it("returns null when the trade fell outside the latest-5-per-bucket window", () => {
    const portfolio = makePortfolio(null);
    expect(findClosedTradeById(portfolio, 999)).toBeNull();
  });
});

describe("buildServerCloseMessage", () => {
  it("builds a stop-loss banner with the exit price/percent and warning tone", () => {
    const trade = makeTrade({ symbol: "FCFS", exit_reason: "STOP_LOSS", exit_price: 211.3, entry_price: 226.71 });
    const result = buildServerCloseMessage(trade, "$");
    expect(result).not.toBeNull();
    expect(result!.tone).toBe("warning");
    expect(result!.message).toContain("FCFS");
    expect(result!.message).toContain("stop loss hit");
    expect(result!.message).toContain("Position closed automatically.");
  });

  it("builds a target-hit banner with success tone", () => {
    const trade = makeTrade({ symbol: "COP", exit_reason: "TARGET_HIT", exit_price: 140.45, entry_price: 129.83 });
    const result = buildServerCloseMessage(trade, "$");
    expect(result!.tone).toBe("success");
    expect(result!.message).toContain("target achieved");
  });

  it("returns null for a manual close — only Auto Close triggers get a server-detected banner", () => {
    const trade = makeTrade({ exit_reason: "MANUAL" });
    expect(buildServerCloseMessage(trade, "$")).toBeNull();
  });
});
