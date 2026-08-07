import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
      current_price: 100, target_price: 120, generated_at: "2026-08-01T00:00:00Z",
      reasoning: [], technical: { overall: "BUY", rsi: 40, macd_diff: 0.1 },
      fundamental_score: { score: 60, reasons: [] },
      sentiment_score: { score: 10, label: "BULLISH", bullish: 0.6, bearish: 0.4 },
      trade_levels: { stop_loss: 90, take_profit: 130, entry_low: 98, entry_high: 102 },
    }),
    fetchPaperPortfolio: vi.fn().mockResolvedValue({ cash: 100000, cash_usd: 100000 }),
  };
});

const { PaperTradeModal } = await import("@/components/PaperTradeModal");

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={() => {}} evidenceSource="RESEARCH"
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  let resolveBuy: (v: unknown) => void = () => {};
  mockPlacePaperBuy.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveBuy = resolve;
        setTimeout(
          () =>
            resolveBuy({
              message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
              cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
              evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
              missing_evidence_fields: [], idempotency_enforced: true,
            }),
          50
        );
      })
  );
});

afterEach(() => cleanup());

describe("PaperTradeModal duplicate-submission protection", () => {
  it("issues exactly one placePaperBuy call for a rapid double click while the mutation is in flight", async () => {
    renderModal();
        const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });

    fireEvent.click(buyButton);
    // Fix 3 (owner-audit correction, Phase A1): all three clicks happen in
    // the same synchronous event-handling pass, before React has committed
    // any `isPending` state update — so this specifically exercises the
    // ref-based `buySubmissionInFlightRef` guard (set synchronously inside
    // the click handler before `mutate` is even called), not merely
    // `buyMutation.isPending`, which is not guaranteed to have flipped yet.
    // The `disabled` attribute below is additional UX defense-in-depth.
    fireEvent.click(buyButton);
    fireEvent.click(buyButton);

    await waitFor(() => expect(screen.getByText(/placing/i)).toBeInTheDocument());
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1), { timeout: 2000 });
  });

  it("disables the Buy button while the mutation is pending", async () => {
    renderModal();
        const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);
    const pendingButton = await screen.findByRole("button", { name: /placing/i });
    expect(pendingButton).toBeDisabled();
  });
});
