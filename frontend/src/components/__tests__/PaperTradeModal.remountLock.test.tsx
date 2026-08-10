// Regression coverage for the REAL production failure mode (TCS double-Buy
// incident), not just a same-instance double-click:
//
//   1. User clicks Buy. The request is in flight (never resolves in this
//      test, simulating a slow/still-processing server response).
//   2. User clicks Cancel (or the header X) — this used to unconditionally
//      call onClose and unmount the modal, destroying its local
//      useMutation/`isPending` state and refs, while leaving the axios POST
//      still in flight server-side with no way to abort it.
//   3. User reopens the modal for the SAME pick (a fresh PaperTradeModal
//      instance/mount, exactly like Daily Picks re-rendering the modal).
//
// Before the fix: step 3 produced a brand-new, blank `useMutation` with
// `isPending === false`, so Buy was clickable again — firing a SECOND POST
// for the same logical operation while the first was still resolving. This
// is the exact mechanism that produced the two open TCS positions.
//
// After the fix (frontend/src/utils/paperBuyLock.ts, wired into
// PaperTradeModal.tsx): the pending state lives in a module-level Map keyed
// by user+market+symbol+original-horizon, not in component state, so it
// survives the unmount. These tests assert the reopened instance
// recognizes the still-pending operation and blocks a second submission.
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

function renderModal(onClose: () => void) {
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

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  __clearAllBuyLocksForTests();
});

afterEach(() => {
  cleanup();
  __clearAllBuyLocksForTests();
});

describe("PaperTradeModal shared in-flight Buy lock (remount survival)", () => {
  it("blocks a second Buy submission after Cancel-then-reopen while the first is still in flight", async () => {
    // Never resolves — simulates the original request still being
    // processed server-side after the modal is closed.
    let resolveFirst: (v: any) => void = () => {};
    mockPlacePaperBuy.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));

    const onClose1 = vi.fn();
    const { unmount } = renderModal(onClose1);
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;

    // Simulate Cancel/X unmounting the modal while the request is still
    // in flight (this is what onClose does in the real app — the parent
    // page stops rendering PaperTradeModal).
    unmount();

    // Reopen the SAME pick — a fresh instance/mount, exactly like the
    // Daily Picks page re-rendering the modal.
    const onClose2 = vi.fn();
    renderModal(onClose2);

    // The Buy button in the reopened instance must be disabled/blocked —
    // this is the crux of the regression: without the shared lock, this
    // fresh instance has isPending===false and would allow a second POST.
    const buyButtonAgain = await screen.findByRole("button", { name: /placing|buy \d+ shares/i });
    fireEvent.click(buyButtonAgain);

    // Give any (incorrect) second submission a chance to fire.
    await new Promise((r) => setTimeout(r, 20));
    expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1);

    // Resolve the original in-flight request now.
    resolveFirst({
      message: "ok", trade_id: 1, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "DAILY_PICK", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });

    // Once resolved, the lock is released — a genuinely NEW Buy click
    // afterwards is allowed and gets a NEW key.
    await waitFor(() => {}, { timeout: 50 }).catch(() => {});
    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 2, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99800, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "DAILY_PICK", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    const onClose3 = vi.fn();
    renderModal(onClose3);
    const buyButton3 = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(buyButton3).not.toBeDisabled(), { timeout: 2000 });
    fireEvent.click(buyButton3);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    const secondKey = mockPlacePaperBuy.mock.calls[1][0].idempotency_key;
    expect(secondKey).not.toBe(firstKey);
  }, 10000);

  it("disables Cancel and the header X while a Buy is in flight", async () => {
    mockPlacePaperBuy.mockImplementationOnce(() => new Promise(() => {})); // never resolves
    const onClose = vi.fn();
    renderModal(onClose);
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    const cancelButton = screen.getByRole("button", { name: "Cancel" });
    expect(cancelButton).toBeDisabled();
    fireEvent.click(cancelButton);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("reuses the SAME idempotency key for a still-pending operation across remount", async () => {
    let resolveFirst: (v: any) => void = () => {};
    mockPlacePaperBuy.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }));
    const { unmount } = renderModal(vi.fn());
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const firstKey = mockPlacePaperBuy.mock.calls[0][0].idempotency_key;
    unmount();

    renderModal(vi.fn());
    // The reopened instance's frozen request (if it were somehow allowed
    // to submit) must carry the SAME key as the still-pending operation —
    // asserted indirectly via the module lock, since the UI itself blocks
    // submission (covered by the previous test).
    const { getBuyLock, buyOperationKey } = await import("@/utils/paperBuyLock");
    const lock = getBuyLock(buyOperationKey("user-1", "IN", "TCS", "medium"));
    expect(lock?.status).toBe("pending");
    expect(lock?.idempotencyKey).toBe(firstKey);

    resolveFirst({
      message: "ok", trade_id: 1, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100,
      cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "DAILY_PICK", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
  });

  it("does not lock an unrelated symbol's Buy", async () => {
    mockPlacePaperBuy.mockImplementationOnce(() => new Promise(() => {})); // TCS never resolves
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const { unmount: unmountTcs } = render(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal symbol="TCS" market="IN" currentPrice={100} signal="BUY" horizon="medium"
          currency="₹" onClose={() => {}} evidenceSource="DAILY_PICK" />
      </QueryClientProvider>
    );
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    await waitFor(() => expect(screen.getByText(/Risk-based suggestion/i)).toBeInTheDocument());
    fireEvent.click(buyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    // TCS's Buy is still pending (never resolves) — do NOT unmount it, to
    // prove a completely separate modal instance (a different symbol) is
    // unaffected by TCS's lock while TCS is still locked.

    mockPlacePaperBuy.mockResolvedValueOnce({
      message: "ok", trade_id: 2, symbol: "INFY", market: "IN", quantity: 1, entry_price: 50,
      cost: 50, remaining_cash: 99950, entry_evidence_captured: true, snapshot_schema_version: "1.0",
      evidence_source: "DAILY_PICK", evidence_completeness: "COMPLETE", available_evidence_fields: [],
      missing_evidence_fields: [], idempotency_enforced: true,
    });
    render(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal symbol="INFY" market="IN" currentPrice={50} signal="BUY" horizon="medium"
          currency="₹" onClose={() => {}} evidenceSource="DAILY_PICK" />
      </QueryClientProvider>
    );
    const infyBuyButtons = await screen.findAllByRole("button", { name: /buy \d+ shares/i });
    const infyBuyButton = infyBuyButtons[infyBuyButtons.length - 1];
    await waitFor(() => expect(infyBuyButton).not.toBeDisabled());
    fireEvent.click(infyBuyButton);
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(2));
    unmountTcs();
  });
});
