import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Fix 6 (owner-audit correction, Phase A1): simulated_execution_price (the
// live/displayed price at Buy time) and recommendation_reference_price (the
// frozen Daily-Pick generation-time reference) must remain genuinely
// distinct fields, fed from distinct sources, when they actually differ —
// never silently collapsed into the same value.

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", email: "u@example.com" } }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

const mockPlacePaperBuy = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    placePaperBuy: (...args: unknown[]) => mockPlacePaperBuy(...args),
    fetchPrediction: vi.fn().mockResolvedValue({
      symbol: "AAPL", market: "US", horizon: "medium", signal: "BUY", confidence: 80,
      current_price: 106, target_price: 120, generated_at: "2026-08-01T00:00:00Z",
      reasoning: [], technical: { overall: "BUY", rsi: 40, macd_diff: 0.1 },
      fundamental_score: { score: 60, reasons: [] },
      sentiment_score: { score: 10, label: "BULLISH", bullish: 0.6, bearish: 0.4 },
      trade_levels: { stop_loss: 90, take_profit: 130, entry_low: 98, entry_high: 102 },
    }),
    fetchPaperPortfolio: vi.fn().mockResolvedValue({ cash: 100000, cash_usd: 100000 }),
  };
});

const { PaperTradeModal } = await import("@/components/PaperTradeModal");
const { buildEntryEvidenceFromDailyPick } = await import("@/utils/entryEvidence");

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  mockPlacePaperBuy.mockResolvedValue({
    message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 106,
    cost: 106, remaining_cash: 99894, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "DAILY_PICK", evidence_completeness: "PARTIAL", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
});

afterEach(() => cleanup());

describe("PaperTradeModal execution price vs recommendation reference price (Fix 6)", () => {
  it("submits a distinct execution price (live) and recommendation_reference_price (frozen generation price) when they genuinely differ", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    // Mirrors how picks/page.tsx wires the modal: currentPrice is the
    // live/displayed price (here 106, simulating a moved quote),
    // referencePrice + entryEvidenceOverride.recommendation_reference_price
    // are both pick.price (100) — the frozen generation-time reference.
    const dailyPickEvidence = buildEntryEvidenceFromDailyPick({
      price: 100, entry_low: 98, entry_high: 102, stop_loss: 90, target: 120,
      confidence: 70, fund_score: 55, sentiment: "NEUTRAL",
      reasoning: [], generated_at: "2026-08-01T00:00:00Z", horizon: "medium",
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal
          symbol="AAPL" market="US" currentPrice={106} referencePrice={100}
          signal="BUY" horizon="medium" currency="$" onClose={() => {}}
          evidenceSource="DAILY_PICK" entryEvidenceOverride={dailyPickEvidence}
        />
      </QueryClientProvider>
    );

    // The distinction banner is shown to the user since the two genuinely differ.
    await waitFor(() => expect(screen.getByText(/Recommendation was generated at/i)).toBeInTheDocument());

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];

    // Execution price: the live price the trade is actually simulated at.
    expect(call.price).toBe(106);
    // Recommendation reference price: the frozen Daily Pick generation-time
    // price, fed independently via entry_evidence — not overwritten by the
    // live execution price.
    expect(call.entry_evidence.recommendation_reference_price).toBe(100);
    expect(call.price).not.toBe(call.entry_evidence.recommendation_reference_price);
  });
});
