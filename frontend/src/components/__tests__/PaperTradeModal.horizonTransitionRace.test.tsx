import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Phase A1.1 final race closure — the horizon-transition window.
//
// `selectedHorizon` flips synchronously the instant the user clicks a
// different horizon button, but the Prediction query for the NEW horizon is
// still in flight. Before this fix, the `active*` derived constants fell
// back to stale props (`initialSignal`, `referencePrice`) or left the
// PRIOR horizon's stop/target still sitting in the (uncleared) input
// fields, and Buy stayed enabled — so a fast click could submit a Medium
// trade carrying Short's stale recommendation context. This suite proves
// the window is closed, and that it closes cleanly again if the user
// switches back to the original frozen Daily Pick horizon before the new
// Prediction resolves.

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", email: "u@example.com" } }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

const mockPlacePaperBuy = vi.fn();
const mockFetchPrediction = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    placePaperBuy: (...args: unknown[]) => mockPlacePaperBuy(...args),
    fetchPrediction: (...args: unknown[]) => mockFetchPrediction(...args),
    fetchPaperPortfolio: vi.fn().mockResolvedValue({ cash: 100000, cash_usd: 100000 }),
  };
});

const { PaperTradeModal } = await import("@/components/PaperTradeModal");
const { buildEntryEvidenceFromDailyPick } = await import("@/utils/entryEvidence");

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  mockPlacePaperBuy.mockResolvedValue({
    message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 372.18,
    cost: 372.18, remaining_cash: 99000, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "DAILY_PICK", evidence_completeness: "PARTIAL", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
  mockFetchPrediction.mockReset();
});

afterEach(() => cleanup());

// A manually-controlled ("deferred") promise so the test can inspect
// mid-flight state rather than racing an auto-resolving mock.
function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

const shortFrozenEvidence = buildEntryEvidenceFromDailyPick({
  price: 372.18, entry_low: 365, entry_high: 373, stop_loss: 355.34, target: 398.01,
  confidence: 70, fund_score: 60, sentiment: "BULLISH",
  reasoning: [{ indicator: "RSI", signal: "BUY", reason: "original short-horizon setup" }],
  generated_at: "2026-08-06T00:00:00Z", horizon: "short",
});

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="AAPL" market="US" currentPrice={372.18} referencePrice={372.18}
        signal="BUY" horizon="short" currency="$" onClose={() => {}}
        evidenceSource="DAILY_PICK" entryEvidenceOverride={shortFrozenEvidence}
      />
    </QueryClientProvider>
  );
}

describe("PaperTradeModal horizon-transition race (Phase A1.1 final closure)", () => {
  it("Test A — Short(frozen) -> click Medium (Prediction held unresolved): stale Short stop/target/reference are not shown/actionable and Buy is blocked; resolving Medium updates the UI and Buy becomes valid with Medium's own data", async () => {
    const mediumDeferred = deferred<any>();
    mockFetchPrediction.mockImplementation((_s: string, _m: string, horizon: string) => {
      if (horizon === "medium") return mediumDeferred.promise;
      return Promise.reject(new Error(`unexpected horizon fetch: ${horizon}`));
    });

    renderModal();

    // Frozen Short state on screen initially.
    await screen.findByDisplayValue("355.34");
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();

    const mediumButton = await screen.findByRole("button", { name: /^medium/i });
    fireEvent.click(mediumButton);

    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalledWith("AAPL", "US", "medium"));

    // Mid-flight: the stale Short numbers must not remain displayed.
    expect(screen.queryByDisplayValue("355.34")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("398.01")).not.toBeInTheDocument();
    // The stale Short reference price must not be presented as Medium's.
    const notice = screen.queryByText(/Recommendation was generated at/i);
    if (notice) {
      expect(notice.textContent).not.toContain("372.18");
    }
    // Buy must be blocked during the transition.
    const buyButtonMidFlight = screen.getByRole("button", { name: /buy \d+ shares|loading/i });
    expect(buyButtonMidFlight).toBeDisabled();
    fireEvent.click(buyButtonMidFlight);
    expect(mockPlacePaperBuy).not.toHaveBeenCalled();

    // Resolve Medium's Prediction with deliberately different values.
    await act(async () => {
      mediumDeferred.resolve({
        symbol: "AAPL", market: "US", horizon: "medium", signal: "SELL", confidence: 33,
        current_price: 250.5, target_price: 260, generated_at: "2026-08-07T00:00:00Z",
        reasoning: [], technical: { overall: "SELL", rsi: 66, macd_diff: -0.2 },
        fundamental_score: { score: 44, reasons: [] },
        sentiment_score: { score: -5, label: "BEARISH", bullish: 0.3, bearish: 0.7 },
        trade_levels: { stop_loss: 240, take_profit: 260, entry_low: 245, entry_high: 252 },
      });
    });

    await screen.findByDisplayValue("240.00");
    expect(screen.getByDisplayValue("260.00")).toBeInTheDocument();

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    expect(buyButton).not.toBeDisabled();
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.horizon).toBe("medium");
    expect(call.stop_loss).toBe(240);
    expect(call.target_price).toBe(260);
    expect(call.entry_evidence.recommendation_signal).toBe("SELL");
    expect(call.entry_evidence.confidence_score).toBe(33);
    expect(call.entry_evidence.recommended_stop_loss).toBe(240);
    expect(call.entry_evidence.recommended_target_price).toBe(260);
    expect(call.entry_evidence.recommendation_reference_price).toBe(250.5);
    // No Short evidence leaks anywhere in the submitted payload.
    expect(call.entry_evidence.confidence_score).not.toBe(70);
    expect(call.stop_loss).not.toBe(355.34);
    expect(call.target_price).not.toBe(398.01);
  });

  it("Test B — switch-back-before-resolution: Short -> Medium (held unresolved) -> back to Short restores the frozen context immediately, and the abandoned Medium promise resolving afterward does not retroactively corrupt state", async () => {
    const mediumDeferred = deferred<any>();
    mockFetchPrediction.mockImplementation((_s: string, _m: string, horizon: string) => {
      if (horizon === "medium") return mediumDeferred.promise;
      return Promise.reject(new Error(`unexpected horizon fetch: ${horizon}`));
    });

    renderModal();
    await screen.findByDisplayValue("355.34");

    const mediumButton = await screen.findByRole("button", { name: /^medium/i });
    fireEvent.click(mediumButton);
    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalledWith("AAPL", "US", "medium"));

    // Still mid-flight — Buy blocked.
    expect(screen.getByRole("button", { name: /buy \d+ shares|loading/i })).toBeDisabled();

    // Switch back to Short BEFORE Medium resolves.
    const shortButton = await screen.findByRole("button", { name: /^short/i });
    fireEvent.click(shortButton);

    // Frozen Short context is active again immediately.
    await screen.findByDisplayValue("355.34");
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    expect(buyButton).not.toBeDisabled();

    // The abandoned Medium promise resolves late.
    await act(async () => {
      mediumDeferred.resolve({
        symbol: "AAPL", market: "US", horizon: "medium", signal: "SELL", confidence: 33,
        current_price: 250.5, target_price: 260, generated_at: "2026-08-07T00:00:00Z",
        reasoning: [], technical: { overall: "SELL", rsi: 66, macd_diff: -0.2 },
        fundamental_score: { score: 44, reasons: [] },
        sentiment_score: { score: -5, label: "BEARISH", bullish: 0.3, bearish: 0.7 },
        trade_levels: { stop_loss: 240, take_profit: 260, entry_low: 245, entry_high: 252 },
      });
    });

    // Still showing Short's frozen values — the late Medium resolution did
    // not retroactively overwrite the now-active Short UI/evidence.
    expect(screen.getByDisplayValue("355.34")).toBeInTheDocument();
    expect(screen.getByDisplayValue("398.01")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /buy \d+ shares/i })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /buy \d+ shares/i }));
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.horizon).toBe("short");
    expect(call.stop_loss).toBe(355.34);
    expect(call.target_price).toBe(398.01);
    expect(call.entry_evidence).toEqual(shortFrozenEvidence);
  });
});
