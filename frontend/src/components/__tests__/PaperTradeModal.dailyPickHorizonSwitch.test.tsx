import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Fix 1 (owner-audit correction, Phase A1) regression: a Daily Pick opened
// at Medium and bought at Long must submit horizon="long" AND entry_evidence
// that is NOT the original Medium Daily Pick's frozen evidence — reusing it
// would silently mislabel the trade as backed by evidence for a
// recommendation the user never actually acted on.

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
    message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
    cost: 100, remaining_cash: 99900, entry_evidence_captured: true, snapshot_schema_version: "1.0",
    evidence_source: "DAILY_PICK", evidence_completeness: "PARTIAL", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
  mockFetchPrediction.mockReset();
  // A genuine Prediction IS fetched for whatever horizon is selected in this
  // flow (the modal's own useQuery is unconditional on evidenceSource) — so
  // the Long-horizon branch below is exercising a real, not fabricated, data
  // path.
  mockFetchPrediction.mockImplementation((_symbol: string, _market: string, horizon: string) =>
    Promise.resolve({
      symbol: "AAPL", market: "US", horizon, signal: "BUY", confidence: 77,
      current_price: 100, target_price: 130, generated_at: "2026-08-01T00:00:00Z",
      reasoning: [], technical: { overall: horizon === "long" ? "STRONG_BUY" : "BUY", rsi: 55, macd_diff: 0.4 },
      fundamental_score: { score: 65, reasons: [] },
      sentiment_score: { score: 20, label: "BULLISH", bullish: 0.7, bearish: 0.3 },
      trade_levels: { stop_loss: 90, take_profit: 130, entry_low: 98, entry_high: 102 },
    })
  );
});

afterEach(() => cleanup());

function renderDailyPickModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const originalMediumPickEvidence = buildEntryEvidenceFromDailyPick({
    price: 100, entry_low: 98, entry_high: 102, stop_loss: 90, target: 120,
    confidence: 70, fund_score: 55, sentiment: "NEUTRAL",
    reasoning: [{ indicator: "RSI", signal: "BUY", reason: "medium-horizon setup" }],
    generated_at: "2026-08-01T00:00:00Z", horizon: "medium",
  });
  render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={() => {}} evidenceSource="DAILY_PICK"
        entryEvidenceOverride={originalMediumPickEvidence}
      />
    </QueryClientProvider>
  );
  return { originalMediumPickEvidence };
}

describe("PaperTradeModal Daily Pick horizon-switch evidence integrity (Fix 1)", () => {
  it("keeps the original frozen evidence when horizon is unchanged (Medium -> Buy at Medium)", async () => {
    const { originalMediumPickEvidence } = renderDailyPickModal();
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.horizon).toBe("medium");
    expect(call.entry_evidence).toEqual(originalMediumPickEvidence);
  });

  it("submits horizon=long AND non-stale evidence when the user switches Medium -> Long before Buy", async () => {
    const { originalMediumPickEvidence } = renderDailyPickModal();

    const longButton = await screen.findByRole("button", { name: /^long/i });
    fireEvent.click(longButton);

    // Wait for the Long-horizon Prediction to load so entry_evidence isn't
    // captured mid-fetch.
    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalledWith("AAPL", "US", "long"));

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];

    expect(call.horizon).toBe("long");
    // Evidence source may honestly remain DAILY_PICK (UX origin), but the
    // evidence payload itself must not be the stale Medium-pick evidence.
    expect(call.evidence_source).toBe("DAILY_PICK");
    expect(call.entry_evidence).not.toEqual(originalMediumPickEvidence);
    // A genuine Long Prediction was available in this flow, so it must be
    // honestly used rather than submitting null/sparse evidence.
    expect(call.entry_evidence.technical_signal).toBe("STRONG_BUY");
    expect(call.entry_evidence.recommendation_reference_price).toBe(100);
  });

  // Test C (Phase A1.1 final decision-context correction) — the stop/target
  // sync leak fix: switching horizon away from the original Daily Pick must
  // sync the stop/target INPUT FIELDS (not just signal/confidence/evidence)
  // to the newly-selected horizon's genuine Prediction.
  it("Test C — switching Medium -> Long syncs stop/target INPUT FIELDS (not just evidence) to the Long Prediction's trade_levels", async () => {
    renderDailyPickModal();

    const longButton = await screen.findByRole("button", { name: /^long/i });
    fireEvent.click(longButton);

    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalledWith("AAPL", "US", "long"));

    // The Long Prediction's trade_levels (stop_loss: 90, take_profit: 130)
    // must now be reflected in the editable stop/target fields.
    await screen.findByDisplayValue("90.00");
    expect(screen.getByDisplayValue("130.00")).toBeInTheDocument();

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.stop_loss).toBe(90);
    expect(call.target_price).toBe(130);
    expect(call.entry_evidence.recommended_stop_loss).toBe(90);
    expect(call.entry_evidence.recommended_target_price).toBe(130);
  });

  // Test D — switch back: Medium -> Long -> back to Medium, no manual
  // edits, must restore the ORIGINAL frozen Medium Pick's stop/target/
  // signal/confidence/reference — not leave the Long Prediction's levels
  // displayed or submitted.
  it("Test D — switching Medium -> Long -> back to Medium restores the original frozen Medium Pick's stop/target, not the leftover Long Prediction's", async () => {
    const { originalMediumPickEvidence } = renderDailyPickModal();

    const longButton = await screen.findByRole("button", { name: /^long/i });
    fireEvent.click(longButton);
    await waitFor(() => expect(mockFetchPrediction).toHaveBeenCalledWith("AAPL", "US", "long"));
    await screen.findByDisplayValue("90.00");

    const mediumButton = await screen.findByRole("button", { name: /^medium/i });
    fireEvent.click(mediumButton);

    // Restored to the frozen original Medium Pick's own stop/target.
    await screen.findByDisplayValue("90.00"); // frozen Medium Pick's stop_loss
    expect(screen.getByDisplayValue("120.00")).toBeInTheDocument(); // frozen Medium Pick's target

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.horizon).toBe("medium");
    expect(call.stop_loss).toBe(90);
    expect(call.target_price).toBe(120);
    expect(call.entry_evidence).toEqual(originalMediumPickEvidence);
  });

  // Test E — manual edit protection: once the user manually edits stop
  // and/or target for the CURRENTLY selected horizon, a background
  // Prediction refetch for that SAME horizon (an automatic source
  // transition with no explicit horizon-switch click — clicking a horizon
  // button is a distinct, deliberate user action that intentionally resets
  // these edits so a genuinely new horizon's recommendation isn't silently
  // paired with a stale manual number) must NOT overwrite the user's
  // values — and the persisted recommended_stop_loss/recommended_target_price
  // (evidence) must independently reflect whatever the Prediction actually
  // said, never the user's manual trade-level edit.
  it("Test E — a manual stop/target edit survives a background same-horizon Prediction refetch, while persisted recommendation evidence stays correct", async () => {
    const { queryClient } = (() => {
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
      const originalMediumPickEvidence = buildEntryEvidenceFromDailyPick({
        price: 100, entry_low: 98, entry_high: 102, stop_loss: 90, target: 120,
        confidence: 70, fund_score: 55, sentiment: "NEUTRAL",
        reasoning: [{ indicator: "RSI", signal: "BUY", reason: "medium-horizon setup" }],
        generated_at: "2026-08-01T00:00:00Z", horizon: "medium",
      });
      render(
        <QueryClientProvider client={queryClient}>
          <PaperTradeModal
            symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
            currency="$" onClose={() => {}} evidenceSource="DAILY_PICK"
            entryEvidenceOverride={originalMediumPickEvidence}
          />
        </QueryClientProvider>
      );
      return { queryClient };
    })();

    await screen.findByRole("button", { name: /buy \d+ shares/i });
    // Inputs on screen, in DOM order: quantity [0], stop loss [1], target price [2].
    const stopLossInput = screen.getAllByRole("spinbutton")[1];
    fireEvent.change(stopLossInput, { target: { value: "77.00" } });
    const targetPriceInput = screen.getAllByRole("spinbutton")[2];
    fireEvent.change(targetPriceInput, { target: { value: "150.00" } });

    expect(screen.getByDisplayValue("77.00")).toBeInTheDocument();
    expect(screen.getByDisplayValue("150.00")).toBeInTheDocument();

    // Note: for an unchanged-horizon DAILY_PICK buy, the stop/target sync
    // effect's active source is the FROZEN Pick (never the live Prediction),
    // so a background Prediction refetch here can't move the fields even
    // without the manual-edit guard — this assertion still proves the
    // manual edit is what's actually submitted, independent of that.
    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    // The trade's OWN submitted stop/target are the user's manual edits.
    expect(call.stop_loss).toBe(77);
    expect(call.target_price).toBe(150);
    // The persisted recommendation evidence is the frozen Medium Pick's own
    // stop/target (90/120) — a SEPARATE fact from the trade's own values,
    // unaffected by the manual edit.
    expect(call.entry_evidence.recommended_stop_loss).toBe(90);
    expect(call.entry_evidence.recommended_target_price).toBe(120);
  });

  // Test E2 — the RESEARCH path (no entryEvidenceOverride at all) is where
  // the stop/target sync effect's active source genuinely IS the live
  // Prediction query, so this is the case that actually exercises the
  // manual-edit guard against a real background Prediction refetch: once
  // the user manually edits stop/target, a same-horizon Prediction refetch
  // that resolves DIFFERENT trade_levels must not overwrite the manual
  // values, while entry_evidence still honestly reflects whatever
  // Prediction the modal actually has (also unaffected by the manual edit).
  it("Test E2 — RESEARCH path: manual stop/target edit survives a same-horizon Prediction refetch that resolves different trade_levels", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    mockFetchPrediction.mockReset();
    mockFetchPrediction.mockResolvedValueOnce({
      symbol: "AAPL", market: "US", horizon: "medium", signal: "BUY", confidence: 77,
      current_price: 100, target_price: 130, generated_at: "2026-08-01T00:00:00Z",
      reasoning: [], technical: { overall: "BUY", rsi: 55, macd_diff: 0.4 },
      fundamental_score: { score: 65, reasons: [] },
      sentiment_score: { score: 20, label: "BULLISH", bullish: 0.7, bearish: 0.3 },
      trade_levels: { stop_loss: 90, take_profit: 130, entry_low: 98, entry_high: 102 },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <PaperTradeModal
          symbol="AAPL" market="US" currentPrice={100} signal="BUY" horizon="medium"
          currency="$" onClose={() => {}} evidenceSource="MANUAL"
        />
      </QueryClientProvider>
    );

    await screen.findByDisplayValue("90.00");

    const stopLossInput = screen.getAllByRole("spinbutton")[1];
    fireEvent.change(stopLossInput, { target: { value: "77.00" } });
    const targetPriceInput = screen.getAllByRole("spinbutton")[2];
    fireEvent.change(targetPriceInput, { target: { value: "150.00" } });

    // A background refetch resolves a genuinely DIFFERENT Prediction for
    // the SAME horizon — no horizon-switch click involved.
    mockFetchPrediction.mockResolvedValueOnce({
      symbol: "AAPL", market: "US", horizon: "medium", signal: "SELL", confidence: 20,
      current_price: 95, target_price: 105, generated_at: "2026-08-02T00:00:00Z",
      reasoning: [], technical: { overall: "SELL", rsi: 25, macd_diff: -0.3 },
      fundamental_score: { score: 40, reasons: [] },
      sentiment_score: { score: -20, label: "BEARISH", bullish: 0.2, bearish: 0.8 },
      trade_levels: { stop_loss: 60, take_profit: 105, entry_low: 90, entry_high: 96 },
    });
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["prediction", "AAPL", "US", "medium"] });
    });

    // Manual edits must still be showing — not overwritten by the refetch.
    expect(screen.getByDisplayValue("77.00")).toBeInTheDocument();
    expect(screen.getByDisplayValue("150.00")).toBeInTheDocument();

    const buyButton = await screen.findByRole("button", { name: /buy \d+ shares/i });
    fireEvent.click(buyButton);

    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    const call = mockPlacePaperBuy.mock.calls[0][0];
    expect(call.stop_loss).toBe(77);
    expect(call.target_price).toBe(150);
    // entry_evidence is built from the (refetched) Prediction independently
    // of the manual trade-level edit — must reflect the SELL/60/105
    // Prediction actually loaded, not the user's 77/150.
    expect(call.entry_evidence.recommended_stop_loss).toBe(60);
    expect(call.entry_evidence.recommended_target_price).toBe(105);
  });
});
