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

function renderModal(props: Partial<React.ComponentProps<typeof PaperTradeModal>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={() => {}} evidenceSource="RESEARCH"
        {...props}
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  mockPlacePaperBuy.mockResolvedValue({
    message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
    cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
});

afterEach(() => cleanup());

describe("PaperTradeModal idempotency key behavior", () => {
  it("sends an idempotency_key and entry_evidence on Buy", async () => {
    renderModal();
        const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    // Ensure the Prediction (and therefore entry_evidence) has actually
    // loaded before clicking Buy — the button itself renders immediately
    // (quantity defaults before the Prediction resolves), so waiting only
    // for the button would race the entry-evidence fetch.
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(typeof call.idempotency_key).toBe("string");
    expect(call.idempotency_key.length).toBeGreaterThanOrEqual(8);
    expect(call.evidence_source).toBe("RESEARCH");
    expect(call.entry_evidence).toBeTruthy();
    expect(call.entry_evidence.recommendation_signal).toBe("BUY");
  });

  it("reuses the same idempotency key on a lost-response retry with unchanged inputs", async () => {
    // First attempt fails (simulating a lost response / transport error).
    mockPlacePaperBuy.mockRejectedValueOnce({ response: { data: { detail: "network error" } } });
    renderModal();
        const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    // Let the prediction/portfolio queries and their auto-fill effects
    // (suggested stop-loss/target/quantity) settle before the first click,
    // so the two clicks below genuinely have identical inputs — otherwise
    // an in-flight auto-fill landing between clicks would legitimately
    // (and correctly, per the "any input change gets a new key" rule)
    // produce a different key, which isn't what this test is isolating.
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    // Second click, nothing changed — must reuse the same key.
    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));

    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).toBe(firstKey);
  });

  it("reuses the same key AND the same original price on a lost-response retry even if the current price changes (Fix 2, case A)", async () => {
    mockPlacePaperBuy.mockRejectedValueOnce({ response: { data: { detail: "network error" } } });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { rerender } = render(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal
          symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
          currency="$" onClose={() => {}} evidenceSource="RESEARCH"
        />
      </QueryClientProvider>
    );
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    // A live quote refresh changes currentPrice before the user retries —
    // this must NOT count as a new logical Buy decision.
    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    rerender(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal
          symbol="AAPL" market="US" currentPrice={107} signal="BUY" horizon="medium"
          currency="$" onClose={() => {}} evidenceSource="RESEARCH"
        />
      </QueryClientProvider>
    );
    const buyButtonAgain = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButtonAgain);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));

    const firstCall = mockPlacePaperBuy.mock.calls[0][0];
    const secondCall = mockPlacePaperBuy.mock.calls[1][0];
    expect(secondCall.idempotency_key).toBe(firstCall.idempotency_key);
    // The retry reuses the ORIGINAL frozen price, not the new live quote.
    expect(secondCall.price).toBe(firstCall.price);
    expect(secondCall.price).toBe(100);
  });

  it("reuses the same key AND the same original entry_evidence on a lost-response retry even if the Prediction changes (Fix 2, case B)", async () => {
    mockPlacePaperBuy.mockRejectedValueOnce({ response: { data: { detail: "network error" } } });
    const { fetchPrediction } = await import("@/utils/api");
    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    // Simulate the underlying Prediction query resolving to a different
    // value between the failed first attempt and the retry (e.g. cache
    // invalidation elsewhere in the app) — the frozen payload from the
    // first attempt must still be reused verbatim.
    (fetchPrediction as any).mockResolvedValueOnce({
      symbol: "AAPL", market: "US", horizon: "medium", signal: "SELL", confidence: 10,
      current_price: 100, target_price: 80, generated_at: "2026-08-02T00:00:00Z",
      reasoning: [], technical: { overall: "SELL", rsi: 90, macd_diff: -3 },
      fundamental_score: { score: 5, reasons: [] },
      sentiment_score: { score: -50, label: "BEARISH", bullish: 0.1, bearish: 0.9 },
      trade_levels: { stop_loss: 95, take_profit: 80, entry_low: 98, entry_high: 102 },
    });

    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));

    const firstCall = mockPlacePaperBuy.mock.calls[0][0];
    const secondCall = mockPlacePaperBuy.mock.calls[1][0];
    expect(secondCall.idempotency_key).toBe(firstCall.idempotency_key);
    expect(secondCall.entry_evidence).toEqual(firstCall.entry_evidence);
    expect(secondCall.entry_evidence.recommendation_signal).toBe("BUY");
  });

  it("uses a new idempotency key when quantity changes before the next Buy click", async () => {
    mockPlacePaperBuy.mockRejectedValueOnce({ response: { data: { detail: "network error" } } });
    renderModal();
        const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    const plusButton = screen.getByRole("button", { name: "+" });
    fireEvent.click(plusButton);

    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 2, entry_price: 100,
      cost: 200, remaining_cash: 99800, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    const buyButtonAgain = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButtonAgain);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));

    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).not.toBe(firstKey);
  });
});
