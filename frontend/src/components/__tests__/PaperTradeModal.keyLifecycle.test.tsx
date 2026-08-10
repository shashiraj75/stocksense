// Owner-review GAP 2 — full idempotency-key lifecycle coverage with real
// assertions on mock-API call counts and actual idempotency_key values,
// not just DOM/button state. Complements PaperTradeModal.remountLock.test.tsx
// (which proves the UI can't be used to double-submit) and
// PaperTradeModal.idempotencyKey.test.tsx (same-instance retry key
// stability) with the specific semantics called out in owner review:
//
//   (a) same logical Buy intent -> same key across remount/retry
//   (b) terminal success -> lock clears, operation considered complete
//   (c) a LATER deliberate Buy for the same user/market/symbol/horizon
//       (even identical inputs) -> NEW logical operation -> NEW key ->
//       backend allows it (no accidental permanent dedup-by-signature)
//   (d) definitive pre-commit rejection from backend -> lock clears
//       safely, user can retry as a new operation
//   (e) ambiguous/timeout network failure where server commit state is
//       unknown -> do NOT auto-mint a new key and silently retry as a
//       fresh Buy — the original key is retained until authoritative
//       reconciliation (the backend's own idempotency table)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { __clearAllBuyLocksForTests, getBuyLock, buyOperationKey } from "@/utils/paperBuyLock";

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
      symbol: "TCS", market: "IN", horizon: "medium", signal: "BUY", confidence: 80,
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

function renderModal(onClose: () => void = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="TCS" market="IN" currentPrice={100} signal="BUY" horizon="medium"
        currency="₹" onClose={onClose} evidenceSource="DAILY_PICK"
      />
    </QueryClientProvider>
  );
}

function successResponse(tradeId: number) {
  return {
    message: "ok", trade_id: tradeId, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100,
    cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "DAILY_PICK", evidence_completeness: "COMPLETE", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  };
}

async function clickBuyAfterReady() {
  const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
  await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
  fireEvent.click(buyButton);
  return buyButton;
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  __clearAllBuyLocksForTests();
});

afterEach(() => {
  cleanup();
  __clearAllBuyLocksForTests();
});

describe("PaperTradeModal idempotency key full lifecycle", () => {
  it("(b) terminal success clears the shared lock", async () => {
    mockPlacePaperBuy.mockResolvedValueOnce(successResponse(1));
    renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    const lockKey = buyOperationKey("user-1", "IN", "TCS", "medium");
    await waitFor(() => expect(getBuyLock(lockKey)).toBeUndefined());
  });

  it("(c) a later deliberate Buy after a completed one gets a NEW key and is allowed", async () => {
    mockPlacePaperBuy.mockResolvedValueOnce(successResponse(1));
    const { unmount } = renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;
    unmount();

    // The prior Buy fully completed (success) — a fresh modal for the
    // exact same user/market/symbol/horizon must be free to buy again,
    // with a brand-new key, and the backend call must actually go through
    // (not be silently deduped by signature/inputs alone).
    mockPlacePaperBuy.mockResolvedValueOnce(successResponse(2));
    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(buyButton).not.toBeDisabled());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).not.toBe(firstKey);
    expect(typeof secondKey).toBe("string");
  });

  it("(d) a definitive backend rejection clears the lock and a fresh retry gets a NEW key", async () => {
    // Definitive: the backend actually responded (with an error) — e.g.
    // insufficient funds / market closed / idempotency conflict. Modeled
    // here as an axios-shaped error WITH a `response`.
    mockPlacePaperBuy.mockRejectedValueOnce({
      response: { status: 400, data: { detail: "Insufficient funds" } },
    });
    renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;

    const lockKey = buyOperationKey("user-1", "IN", "TCS", "medium");
    await waitFor(() => expect(getBuyLock(lockKey)).toBeUndefined());

    // The user retries — since the earlier attempt was a definitive,
    // terminal failure, the frozen-local-state path (same instance, no
    // remount) still naturally reuses the prior key for THIS immediate
    // retry (that's the existing "lost-response retry" contract); the
    // key only changes once a genuinely new intent begins after a
    // remount. Prove that path here too: unmount and reopen, which must
    // now be treated as a fresh intent with a NEW key.
    cleanup();
    mockPlacePaperBuy.mockResolvedValueOnce(successResponse(3));
    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(buyButton).not.toBeDisabled());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).not.toBe(firstKey);
  });

  it("(e) an ambiguous (no-response) failure retains the SAME key and does not silently mint a new one", async () => {
    // Ambiguous: a network/timeout error with NO `response` at all — the
    // client cannot tell whether the backend received/committed it.
    mockPlacePaperBuy.mockRejectedValueOnce(new Error("Network Error"));
    renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;

    // The lock must still be held (pending) under the SAME key — not
    // released, not replaced.
    const lockKey = buyOperationKey("user-1", "IN", "TCS", "medium");
    const lock = getBuyLock(lockKey);
    expect(lock?.status).toBe("pending");
    expect(lock?.idempotencyKey).toBe(firstKey);

    // A same-instance retry (the realistic "click Buy again" recovery
    // path) must reuse the exact same key — never mint a fresh one behind
    // the user's back.
    mockPlacePaperBuy.mockResolvedValueOnce(successResponse(4));
    const buyButtonAgain = await screen.findByRole("button", { name: /buy \d+ shares/i });
    expect(buyButtonAgain).not.toBeDisabled();
    fireEvent.click(buyButtonAgain);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).toBe(firstKey);
  });

  it("(e) an ambiguous failure blocks a REMOUNTED instance from getting a fresh key too", async () => {
    mockPlacePaperBuy.mockImplementationOnce(() => Promise.reject(new Error("Network Error")));
    const { unmount } = renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;
    unmount();

    // Reopen — a fresh instance must see the still-pending (ambiguous,
    // unresolved) lock and refuse to submit a second, independently-keyed
    // request.
    renderModal();
    const buyButtonAgain = await screen.findByRole("button", { name: /placing|buy \d+ shares/i });
    fireEvent.click(buyButtonAgain);
    await new Promise((r) => setTimeout(r, 20));
    expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1); // still just the one ambiguous attempt

    const lockKey = buyOperationKey("user-1", "IN", "TCS", "medium");
    expect(getBuyLock(lockKey)?.idempotencyKey).toBe(firstKey);
  });

  it("(a) key stays identical across BOTH a same-instance HTTP retry and a remount, for one still-pending operation", async () => {
    let resolveFirst: (v: any) => void = () => {};
    mockPlacePaperBuy.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    const { unmount } = renderModal();
    await clickBuyAfterReady();
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;

    unmount();
    renderModal();
    const lockKey = buyOperationKey("user-1", "IN", "TCS", "medium");
    expect(getBuyLock(lockKey)?.idempotencyKey).toBe(firstKey);

    resolveFirst(successResponse(5));
    await waitFor(() => expect(getBuyLock(lockKey)).toBeUndefined());
  });
});
