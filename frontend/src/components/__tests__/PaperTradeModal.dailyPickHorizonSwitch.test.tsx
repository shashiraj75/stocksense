import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
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
});
