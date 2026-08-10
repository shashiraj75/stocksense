// POST-SUCCESS SAME-MODAL REACTIVATION RACE regression test.
//
// Reproduces the exact BMY production sequence: one still-mounted modal,
// one successful Buy, and — BEFORE the modal's deferred onClose/unmount
// (PaperTradeModal.tsx calls `setTimeout(onClose, 1500)` in onSuccess) —
// a second click on the SAME still-rendered Buy button. Pre-fix, the Buy
// button's `disabled` expression checked only `buyMutation.isPending` (plus
// unrelated lock/market flags), and `isPending` (and the shared cross-
// instance lock) settle back to "not blocking" the instant the mutation
// resolves — well before the 1.5s deferred `onClose` actually unmounts the
// modal — so the button re-renders as enabled and a second click mints a
// fresh idempotency key and fires a second real `placePaperBuy` call. This
// test asserts the mock call count itself (not just button text/attributes)
// never exceeds 1, and MUST fail against the pre-fix implementation.
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

// onClose deliberately does NOT unmount the modal in this test — the whole
// point is to hold the modal mounted through the post-success window
// (matching the real component's own `setTimeout(onClose, 1500)` behavior,
// where the modal stays mounted for 1.5s after success) and prove the Buy
// button cannot be fired again during that window, independent of when the
// parent eventually honors onClose.
const onCloseSpy = vi.fn();

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="BMY" market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={onCloseSpy} evidenceSource="RESEARCH"
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  onCloseSpy.mockReset();
  let callIndex = 0;
  mockPlacePaperBuy.mockImplementation(
    () =>
      new Promise((resolve) => {
        callIndex += 1;
        const tradeId = callIndex;
        setTimeout(
          () =>
            resolve({
              message: "ok", trade_id: tradeId, symbol: "BMY", market: "US", quantity: 1, entry_price: 100,
              cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
              evidence_source: "RESEARCH", evidence_completeness: "COMPLETE", available_evidence_fields: [],
              missing_evidence_fields: [], idempotency_enforced: true,
            }),
          20
        );
      })
  );
});

afterEach(() => cleanup());

describe("PaperTradeModal post-success same-modal reactivation race (BMY defect)", () => {
  it("does not allow a second Buy submission from the same still-mounted modal after the first Buy succeeds", async () => {
    renderModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });

    // 1. First, single, legitimate Buy click.
    fireEvent.click(buyButton);
    await waitFor(() => expect(screen.getByText(/placing/i)).toBeInTheDocument());
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));

    // 2. Let the first request resolve successfully. The component does
    // NOT unmount here — onClose is only invoked via a 1.5s setTimeout
    // inside the component itself, and this test's onClose stub is a no-op
    // that doesn't remove the tree — so we are now heading into the exact
    // intermediate state from the BMY recording: mutation settled
    // (isPending -> false), modal still fully mounted and rendered.
    await waitFor(() => expect(screen.getByText(/bought 1/i)).toBeInTheDocument());

    // The modal's own onSuccess releases the shared cross-instance lock
    // (releaseBuyLock) synchronously, but that release doesn't itself
    // trigger a React re-render (it mutates a module-level Map, not React
    // state) — a subsequent state-driven render is what actually reflects
    // it. Settle across several real ticks (comfortably past the 750ms
    // lock-recheck poll interval the component itself uses) so this test
    // lands in the same post-success-but-still-mounted window the BMY user
    // actually clicked in, not just the very first paint after success.
    for (let i = 0; i < 5; i++) {
      // eslint-disable-next-line no-await-in-loop
      await new Promise((r) => setTimeout(r, 100));
    }

    // 3. This is the crux of the regression: at this point — mutation long
    // since settled, modal still fully mounted, nothing else pending — the
    // Buy button must NOT read as an executable "Buy N shares" button
    // again. Pre-fix this fails: the button settles into "Buy 1 shares",
    // enabled.
    expect(screen.queryByRole("button", { name: /buy \d+ shares/i })).not.toBeInTheDocument();

    // 4. Simulate the second click the BMY user made on the same
    // still-visible button (query broadly, not gated on accessible name,
    // to also catch the pre-fix case where the button's name reverted to
    // "Buy 1 shares").
    const buttons = screen.getAllByRole("button");
    const stillSameBuyButton = buttons.find((b) => /buy|placing/i.test(b.textContent ?? ""));
    expect(stillSameBuyButton).toBeTruthy();
    fireEvent.click(stillSameBuyButton!);

    // Give any accidental second mutation a chance to fire and resolve.
    await new Promise((r) => setTimeout(r, 100));

    // 5. The actual assertion that matters: no second real HTTP-layer call.
    // Pre-fix, step 4's click would pass through disabled={isPending || ...}
    // (false, since the first mutation already settled and the lock was
    // released) and the onClick guard `if (buySubmissionInFlightRef.current
    // || buyMutation.isPending || sellMutation.isPending) return;` (also
    // false, since onSuccess resets buySubmissionInFlightRef to false and
    // isPending is false), reaching `buyMutation.mutate(req)` a second time
    // with a freshly minted idempotency key (frozenBuyRequestRef was
    // cleared in onSuccess) — producing a second real placePaperBuy call.
    // Post-fix, the `buySucceededTerminal` latch (set synchronously and
    // FIRST inside onSuccess) blocks both the `disabled` attribute and the
    // onClick handler's own guard, so the call count must stay at 1.
    expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1);
  });
});
