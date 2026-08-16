// Additional regression coverage for the POST-SUCCESS SAME-MODAL
// REACTIVATION RACE fix (Phase 9): idempotency-key lifecycle preservation,
// definitive/ambiguous failure retry behavior, unrelated-symbol
// independence, and post-unmount fresh-operation behavior. The core
// same-modal reactivation case itself is covered by
// PaperTradeModal.postSuccessReactivation.test.tsx.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { __clearAllBuyLocksForTests } from "@/utils/paperBuyLock";

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
      symbol: "BMY", market: "US", horizon: "medium", signal: "BUY", confidence: 80,
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

function renderModal(symbol = "BMY", onClose: () => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol={symbol} market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={onClose} evidenceSource="RESEARCH"
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  __clearAllBuyLocksForTests();
});

afterEach(() => cleanup());

describe("PaperTradeModal terminal-state invariants (Phase 9)", () => {
  it("definitive failure still allows a same-instance retry with the same idempotency key", async () => {
    let firstKey: string | undefined;
    let secondKey: string | undefined;
    let call = 0;
    mockPlacePaperBuy.mockImplementation((req: { idempotency_key: string }) => {
      call += 1;
      if (call === 1) {
        firstKey = req.idempotency_key;
        const err: any = new Error("insufficient funds");
        err.response = { data: { detail: "Insufficient funds" }, status: 400 };
        return Promise.reject(err);
      }
      secondKey = req.idempotency_key;
      return Promise.resolve({
        message: "ok", trade_id: 1, symbol: "BMY", market: "US", quantity: 1, entry_price: 100,
        cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
        evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
        missing_evidence_fields: [], idempotency_enforced: true,
      });
    });

    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(screen.getByText(/insufficient funds/i)).toBeInTheDocument());

    const retryButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(retryButton);

    await waitFor(() => expect(screen.getByText(/bought 1/i)).toBeInTheDocument());
    expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2);
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
  });

  it("ambiguous (network) failure retains the same key and does not silently mint a new one", async () => {
    let firstKey: string | undefined;
    let secondKey: string | undefined;
    let call = 0;
    mockPlacePaperBuy.mockImplementation((req: { idempotency_key: string }) => {
      call += 1;
      if (call === 1) {
        firstKey = req.idempotency_key;
        return Promise.reject(new Error("Network Error"));
      }
      secondKey = req.idempotency_key;
      return Promise.resolve({
        message: "ok", trade_id: 1, symbol: "BMY", market: "US", quantity: 1, entry_price: 100,
        cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
        evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
        missing_evidence_fields: [], idempotency_enforced: true,
      });
    });

    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(screen.getByText(/failed to place trade/i)).toBeInTheDocument());

    const retryButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(retryButton);

    await waitFor(() => expect(screen.getByText(/bought 1/i)).toBeInTheDocument());
    expect(secondKey).toBe(firstKey);
  });

  it("an unrelated symbol's modal remains independently actionable after another symbol's Buy succeeds", async () => {
    mockPlacePaperBuy.mockImplementation((req: { symbol: string }) =>
      Promise.resolve({
        message: "ok", trade_id: 1, symbol: req.symbol, market: "US", quantity: 1, entry_price: 100,
        cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
        evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
        missing_evidence_fields: [], idempotency_enforced: true,
      })
    );

    const { unmount } = renderModal("BMY");
    const bmyBuyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(bmyBuyButton);
    await waitFor(() => expect(screen.getByText(/bought 1/i)).toBeInTheDocument());
    unmount();

    renderModal("AAPL");
    const aaplBuyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    expect(aaplBuyButton).not.toBeDisabled();
    fireEvent.click(aaplBuyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
  });

  it("after full unmount, a genuinely new later Buy starts a new logical operation with a new key", async () => {
    const keys: string[] = [];
    mockPlacePaperBuy.mockImplementation((req: { idempotency_key: string }) => {
      keys.push(req.idempotency_key);
      return Promise.resolve({
        message: "ok", trade_id: keys.length, symbol: "BMY", market: "US", quantity: 1, entry_price: 100,
        cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
        evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
        missing_evidence_fields: [], idempotency_enforced: true,
      });
    });

    const { unmount } = renderModal("BMY");
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);
    await waitFor(() => expect(screen.getByText(/bought 1/i)).toBeInTheDocument());
    unmount();

    renderModal("BMY");
    const buyButton2 = await screen.findByRole("button", { name: /buy \d+ shares/i });
    expect(buyButton2).not.toBeDisabled();
    fireEvent.click(buyButton2);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    expect(keys.length).toBe(2);
    expect(keys[0]).not.toBe(keys[1]);
  });
});
