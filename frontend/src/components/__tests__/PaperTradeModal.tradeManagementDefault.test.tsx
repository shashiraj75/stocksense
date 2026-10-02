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
      current_price: 100, target_price: 130, generated_at: "2026-08-01T00:00:00Z",
      reasoning: [], technical: { overall: "BUY", rsi: 45, macd_diff: 0.1 },
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
        currency="$" suggestedStopLoss={90} suggestedTargetPrice={130}
        onClose={() => {}} evidenceSource="RESEARCH"
      />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockPlacePaperBuy.mockReset();
  mockPlacePaperBuy.mockResolvedValue({
    message: "ok", trade_id: 1, symbol: "AAPL", market: "US", quantity: 1, entry_price: 100,
    cost: 100, remaining_cash: 99900, entry_evidence_captured: true,
    snapshot_schema_version: "1.0", evidence_source: "RESEARCH",
    evidence_completeness: "PARTIAL", available_evidence_fields: [],
    missing_evidence_fields: [], idempotency_enforced: true,
  });
});

afterEach(() => cleanup());

describe("PaperTradeModal trade management selection", () => {
  it("shows Auto first, selected by default, while Manual remains available and AI disabled", () => {
    renderModal();
    const controlGrid = screen.getByText("Trade Management").nextElementSibling;
    expect(controlGrid).not.toBeNull();
    const cards = Array.from(controlGrid!.querySelectorAll("button"));
    expect(cards.map((button) => button.querySelector("p")?.textContent)).toEqual([
      "Auto", "Manual", "AI",
    ]);
    expect(cards[0]).toHaveClass("border-brand-500");
    expect(cards[1]).not.toHaveClass("border-brand-500");
    expect(cards[2]).toBeDisabled();
  });

  it("submits Auto by default with the user's visible stop-loss and target-price values", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /buy \d+ shares/i }));
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    expect(mockPlacePaperBuy).toHaveBeenCalledWith(expect.objectContaining({
      trade_management_mode: "auto", stop_loss: 90, target_price: 130,
    }));
  });

  it("preserves explicit Manual selection in the submitted trade", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /^manual/i }));
    expect(screen.getByRole("button", { name: /^manual/i })).toHaveClass("border-brand-500");
    expect(screen.getByRole("button", { name: /^auto/i })).not.toHaveClass("border-brand-500");
    fireEvent.click(screen.getByRole("button", { name: /buy \d+ shares/i }));
    await waitFor(() => expect(mockPlacePaperBuy).toHaveBeenCalledTimes(1));
    expect(mockPlacePaperBuy).toHaveBeenCalledWith(expect.objectContaining({
      trade_management_mode: "manual",
    }));
  });

  it("uses Auto again on a fresh modal mount, not a prior unsubmitted Manual choice", () => {
    const previous = renderModal();
    fireEvent.click(screen.getByRole("button", { name: /^manual/i }));
    previous.unmount();
    renderModal();
    expect(screen.getByRole("button", { name: /^auto/i })).toHaveClass("border-brand-500");
  });
});
