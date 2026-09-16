// 2026-09-16 user report: on mobile, the page behind this modal could
// still scroll independently of the modal's own internal scrollable body,
// so the sticky Buy/Cancel footer could end up below the fold with no
// visible way to reach it until the page itself happened to scroll too
// (reproduced on a taller US-stock modal whose extra "Using latest market
// price" notice pushed content past the viewport). Fix: lock
// document.body scroll while the modal is mounted, restoring whatever
// value body had before on unmount.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", email: "u@example.com" } }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchPrediction: vi.fn().mockResolvedValue({
      symbol: "ING", market: "US", horizon: "medium", signal: "BUY", confidence: 80,
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

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PaperTradeModal
        symbol="ING" market="US" currentPrice={100} signal="BUY" horizon="medium"
        currency="$" onClose={() => {}} evidenceSource="RESEARCH"
      />
    </QueryClientProvider>
  );
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

describe("PaperTradeModal — body scroll lock", () => {
  it("locks document.body scroll while mounted", () => {
    expect(document.body.style.overflow).toBe("");
    renderModal();
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("restores the prior body overflow value on unmount, not an unconditional reset", () => {
    document.body.style.overflow = "scroll"; // simulate a page that had its own reason to set this
    const { unmount } = renderModal();
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe("scroll");
  });
});
