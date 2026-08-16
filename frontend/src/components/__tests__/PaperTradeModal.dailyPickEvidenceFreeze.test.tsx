import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Phase A1.1 (Production trade 304 correction) regression coverage.
//
// Production trade 304 (AMG, Daily Pick Buy) showed the owner a modal with
// horizon=short, signal=BUY, confidence=70%, execution price=371.76,
// reference price=372.18, stop=355.34, target=398.01 — but the persisted
// immutable entry snapshot recorded confidence_score=100,
// recommended_stop_loss=350, recommended_target_price=409.14. Root cause:
// `entryEvidenceOverride`/`suggestedStopLoss`/`suggestedTargetPrice`/
// `referencePrice` were built INLINE in JSX in picks/page.tsx on every
// render, reading the live Picks-list react-query `pick` object — which
// refetches every 60s–5min and can change while the PaperTradeModal is
// open, before the user clicks Buy.
//
// The fix: `PickCard` (frontend/src/app/picks/page.tsx) now captures ONE
// immutable `frozenPick` snapshot in state at the moment the modal is
// opened (`setFrozenPick(pick)`), and the modal is rendered exclusively
// from `frozenPick`, never the live `pick` prop, for as long as it stays
// open. This test renders `PickCard` directly (now exported for this
// purpose) and simulates a parent Picks-list refetch by re-rendering it
// with a DIFFERENT `pick` prop while the modal stays mounted — reproducing
// the exact production scenario end-to-end, including what actually gets
// submitted to `placePaperBuy`.

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", email: "u@example.com" } }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

const mockPlacePaperBuy = vi.fn();
const mockFetchPrediction = vi.fn();
const mockFetchQuote = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    placePaperBuy: (...args: unknown[]) => mockPlacePaperBuy(...args),
    fetchPrediction: (...args: unknown[]) => mockFetchPrediction(...args),
    fetchPaperPortfolio: vi.fn().mockResolvedValue({ cash: 100000, cash_usd: 100000 }),
    fetchQuote: (...args: unknown[]) => mockFetchQuote(...args),
  };
});

import type { Pick } from "@/app/picks/page";
const { PickCard } = await import("@/app/picks/page");

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  mockPlacePaperBuy.mockResolvedValue({
    message: "ok", trade_id: 304, symbol: "AMG", market: "US", quantity: 1, entry_price: 371.76,
    cost: 371.76, remaining_cash: 99000, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "DAILY_PICK", evidence_completeness: "PARTIAL", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
  // A live Prediction query fires unconditionally in the background
  // (unrelated to evidenceSource) — deliberately different across EVERY
  // dimension (signal, confidence, reference price, stop, target) from the
  // frozen Daily Pick's own BUY/70/372.18/355.34/398.01, so this test is a
  // real proof of the invariant rather than a dodge. An earlier version of
  // this fixture made trade_levels equal the frozen Pick's own stop/target
  // specifically to avoid tripping the (at-the-time-unfixed) live-Prediction
  // stop/target leak in the auto-fill effect — that made the test pass
  // whether or not the leak existed. With fully-distinct values here, the
  // assertions below only pass once the leak is genuinely closed.
  mockFetchPrediction.mockResolvedValue({
    symbol: "AMG", market: "US", horizon: "short", signal: "SELL", confidence: 12,
    current_price: 350.00, target_price: 400, generated_at: "2026-08-06T00:00:00Z",
    reasoning: [], technical: { overall: "SELL", rsi: 30, macd_diff: -0.4 },
    fundamental_score: { score: 40, reasons: [] },
    sentiment_score: { score: -10, label: "BEARISH", bullish: 0.2, bearish: 0.8 },
    trade_levels: { stop_loss: 350, take_profit: 409.14, entry_low: 340, entry_high: 355 },
  });
  // No live quote — displayPrice falls back to the frozen pick's price,
  // matching the production trace's execution price basis.
  mockFetchQuote.mockResolvedValue({ price: 371.76, quote_price_basis: "current", quote_timestamp: "2026-08-07T00:00:00Z" });
});

afterEach(() => cleanup());

const originalPick: Pick = {
  symbol: "AMG", name: "AMG Corp", price: 372.18, target: 398.01, stop_loss: 355.34,
  entry_low: 365, entry_high: 373, confidence: 70, fund_score: 60, sentiment: "BULLISH",
  reasoning: [{ indicator: "RSI", signal: "BUY", reason: "original short-horizon setup" }],
  horizon: "short", generated_at: "2026-08-06T00:00:00Z",
  generation_reference_price: 372.18, generation_reference_source: "close",
  generation_reference_price_basis: "close",
};

const refreshedPick: Pick = {
  ...originalPick,
  target: 409.14, stop_loss: 350, confidence: 100,
  reasoning: [{ indicator: "RSI", signal: "BUY", reason: "refreshed run setup" }],
};

function renderCard(pick: Pick) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <PickCard pick={pick} rank={1} market="US" currency="$" locale="en-US" />
    </QueryClientProvider>
  );
  return { ...utils, queryClient };
}

// The modal's AI Signal pill renders confidence as
// `<span className="opacity-60 ml-auto">{activeConfidence}%</span>` — two
// separate text nodes ("70" and "%"), so a plain getByText("70%") never
// matches. Read the pill's confidence span directly instead. Scoped to
// `.opacity-60.ml-auto` inside the modal (the `fixed inset-0 z-[60] ...`
// overlay) so it can't accidentally match the card's own always-static
// "Signal Strength" percentage, which lives in a different element.
function readModalPillConfidenceText(container: HTMLElement): string | null {
  const modal = container.querySelector(".z-\\[60\\]");
  return modal?.querySelector(".opacity-60.ml-auto")?.textContent ?? null;
}

describe("PickCard Daily Pick decision-evidence freeze at modal-open (Phase A1.1 / trade 304)", () => {
  it("keeps the modal showing the ORIGINAL frozen Daily Pick numbers, and submits them unchanged, even after the parent Picks list refreshes to a different pick while the modal stays open", async () => {
    const { rerender, queryClient, container } = renderCard(originalPick);

    const openButton = await screen.findByRole("button", { name: /paper trade/i });
    fireEvent.click(openButton);

    // Modal open — shows the original frozen confidence/stop/target.
    await screen.findByDisplayValue("355.34");
    expect(readModalPillConfidenceText(container)).toBe("70%");
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();

    // Simulate the parent Picks list's react-query refetch delivering a
    // DIFFERENT pick object for the same symbol (confidence=100,
    // stop=350, target=409.14) while the modal remains mounted — exactly
    // what happened in production before the fix.
    act(() => {
      rerender(
        <QueryClientProvider client={queryClient}>
          <PickCard pick={refreshedPick} rank={1} market="US" currency="$" locale="en-US" />
        </QueryClientProvider>
      );
    });

    // The modal must still show the ORIGINAL numbers — nothing the
    // refreshed Picks list did in the background should have altered
    // what's displayed for a modal that's already open.
    expect(readModalPillConfidenceText(container)).toBe("70%");
    expect(screen.getByDisplayValue("355.34")).toBeInTheDocument();
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();

    // User does NOT change horizon, then clicks Buy.
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];

    // The submitted recommendation evidence must be the ORIGINAL frozen
    // numbers — the refreshed pick must not leak into what's persisted.
    expect(call.entry_evidence.confidence_score).toBe(70);
    expect(call.entry_evidence.recommended_stop_loss).toBe(355.34);
    expect(call.entry_evidence.recommended_target_price).toBe(398.01);
    expect(call.entry_evidence.recommendation_reference_price).toBe(372.18);
    // The trade's OWN submitted stop/target (from the input fields) must
    // also be the frozen Pick's — never the live Prediction's SELL/12
    // trade_levels (stop=350, target=409.14), and never the refreshed
    // pick's values either.
    expect(call.stop_loss).toBe(355.34);
    expect(call.target_price).toBe(398.01);
  });

  it("displays and persists the frozen Daily Pick's own signal/confidence/stop/target for the AI Signal pill and inputs, not the live/unrelated Prediction query's (exact production-bug variant, Test A)", async () => {
    const { container } = renderCard(originalPick);

    const openButton = await screen.findByRole("button", { name: /paper trade/i });
    fireEvent.click(openButton);

    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalled());
    // Give the live Prediction query a tick to resolve, so a would-be leak
    // (of signal, confidence, stop, or target) has every opportunity to
    // show up before we assert none of it did.
    await waitFor(() => expect(readModalPillConfidenceText(container)).not.toBeNull());

    // The frozen Pick's BUY/70% must be shown — never the live
    // Prediction's SELL/12%.
    expect(readModalPillConfidenceText(container)).toBe("70%");
    expect(screen.getByText("BUY")).toBeInTheDocument();
    expect(screen.queryByText("SELL")).not.toBeInTheDocument();

    // The stop-loss/target-price INPUT FIELDS must show the frozen Pick's
    // own 355.34/398.01 — never the live Prediction's 350/409.14
    // trade_levels. This is the exact leak this correction pass closes.
    expect(screen.getByDisplayValue("355.34")).toBeInTheDocument();
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("350.00")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("409.14")).not.toBeInTheDocument();

    // The reference-price notice (when shown) must also cite the frozen
    // Pick's own reference price, never the live Prediction's current_price.
    const notice = screen.queryByText(/Recommendation was generated at/i);
    if (notice) {
      expect(notice.textContent).toContain("372.18");
      expect(notice.textContent).not.toContain("350");
    }

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.signal).toBe("BUY");
    expect(call.stop_loss).toBe(355.34);
    expect(call.target_price).toBe(398.01);
    expect(call.entry_evidence.recommendation_signal).toBe("BUY");
    expect(call.entry_evidence.confidence_score).toBe(70);
    expect(call.entry_evidence.recommended_stop_loss).toBe(355.34);
    expect(call.entry_evidence.recommended_target_price).toBe(398.01);
    expect(call.entry_evidence.recommendation_reference_price).toBe(372.18);
  });

  it("Test F — copy integrity: mutating/replacing the source Pick object after modal-open does not affect the already-frozen decision context", async () => {
    // A fresh, test-local clone of `originalPick` — NOT the shared module
    // const used by the other tests in this file — so the in-place mutation
    // below can never bleed into other tests regardless of run order.
    const localSourcePick: Pick = {
      ...originalPick,
      reasoning: originalPick.reasoning.map(r => ({ ...r })),
    };
    const { rerender, queryClient, container } = renderCard(localSourcePick);

    const openButton = await screen.findByRole("button", { name: /paper trade/i });
    fireEvent.click(openButton);
    await screen.findByDisplayValue("355.34");

    // Mutate the ORIGINAL source object's fields directly (simulating a
    // buggy caller or an in-place cache mutation) — a true copy-based
    // snapshot must be entirely unaffected by this.
    (localSourcePick as any).confidence = 999;
    (localSourcePick as any).stop_loss = 1;
    (localSourcePick as any).target = 2;
    (localSourcePick as any).reasoning[0].reason = "mutated after freeze";

    // Also simulate the parent delivering a brand-new object for the same
    // symbol (the other way a live cache update could reach the modal).
    const mutatedPick: Pick = { ...localSourcePick, confidence: 999, stop_loss: 1, target: 2 };
    act(() => {
      rerender(
        <QueryClientProvider client={queryClient}>
          <PickCard pick={mutatedPick} rank={1} market="US" currency="$" locale="en-US" />
        </QueryClientProvider>
      );
    });

    // The already-open modal's frozen context must be entirely unaffected.
    expect(readModalPillConfidenceText(container)).toBe("70%");
    expect(screen.getByDisplayValue("355.34")).toBeInTheDocument();
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.entry_evidence.confidence_score).toBe(70);
    expect(call.entry_evidence.recommended_stop_loss).toBe(355.34);
    expect(call.entry_evidence.recommended_target_price).toBe(398.01);
    expect(call.entry_evidence.recommendation_reasoning[0].reason).toBe("original short-horizon setup");
  });
});
