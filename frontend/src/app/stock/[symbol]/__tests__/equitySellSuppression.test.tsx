import { describe, it, expect, vi, afterEach } from "vitest";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
global.ResizeObserver = ResizeObserverStub;
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

let mockSymbol = "RELIANCE";
let mockMarket = "IN";
vi.mock("next/navigation", () => ({
  useParams: () => ({ symbol: mockSymbol }),
  useSearchParams: () => new URLSearchParams({ market: mockMarket }),
}));
vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: null }),
}));

const mockFetchQuote = vi.fn();
const mockFetchPrediction = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchQuote: (...args: unknown[]) => mockFetchQuote(...args),
    fetchPrediction: (...args: unknown[]) => mockFetchPrediction(...args),
    fetchNews: vi.fn().mockResolvedValue({ articles: [] }),
    fetchFactorAttribution: vi.fn().mockResolvedValue({ contributions: [], composite_score: 0, positive_total: 0, negative_total: 0 }),
    fetchScoreHistory: vi.fn().mockResolvedValue({ symbol: "", horizon: "medium", window_days: 90, points: [] }),
    api: { ...actual.api, get: vi.fn().mockResolvedValue({ data: { accuracy: {} } }) },
  };
});

const { default: StockPage } = await import("../page");

function renderPage() {
  // Pre-acknowledge the market disclaimer modal (MarketDisclaimer.tsx) so
  // it doesn't block the prediction UI under test — unrelated to this
  // PR's suppression-display behavior.
  window.localStorage.setItem(`stocksense_disclaimer_ack_${mockMarket}_v1`, "1");
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StockPage />
    </QueryClientProvider>
  );
}

const BASE_QUOTE = {
  symbol: "RELIANCE", price: 1307.8, change: 27.4, change_pct: 2.14,
  high: 1311.1, low: 1287.8, fifty_two_week_high: 1611.8, fifty_two_week_low: 1253.2,
  market_cap: 17700000000000, volume: 16744303, company_name: "Reliance Industries Limited",
};

// 2026-09-06 PR #85 corrective follow-up — the stock page must visibly
// distinguish a HOLD that would previously have been an actionable SELL
// (equity_sell_suppressed=true) from an ordinary, genuinely-neutral HOLD.
describe("Stock Detail — equity SELL suppression display", () => {
  afterEach(() => {
    vi.clearAllMocks();
    mockSymbol = "RELIANCE";
    mockMarket = "IN";
  });

  it("shows the Suppressed SELL callout for a HOLD with equity_sell_suppressed=true", async () => {
    mockFetchQuote.mockResolvedValue(BASE_QUOTE);
    mockFetchPrediction.mockResolvedValue({
      symbol: "RELIANCE", market: "IN", horizon: "short",
      signal: "HOLD", confidence: 6, current_price: 1307.8, target_price: 1283.0,
      equity_sell_suppressed: true,
      equity_sell_suppressed_note:
        "This setup would previously have been classified SELL. Equity SELL recommendations are currently disabled pending methodology review — this is a containment state, not an affirmative recommendation to hold the position.",
      reasoning: [], technical: { overall: "HOLD", rsi: 50, macd_diff: -0.1 },
      fundamental_score: { score: 40, reasons: [] },
      sentiment_score: { score: 50, label: "neutral", bullish: 1, bearish: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText((_, el) => el?.textContent === "— HOLD")).toBeInTheDocument());
    expect(await screen.findByText("Suppressed SELL")).toBeInTheDocument();
    expect(await screen.findByText(/This setup would previously have been classified SELL/)).toBeInTheDocument();
  });

  it("does NOT show the Suppressed SELL callout for an ordinary, non-suppressed HOLD", async () => {
    mockFetchQuote.mockResolvedValue(BASE_QUOTE);
    mockFetchPrediction.mockResolvedValue({
      symbol: "RELIANCE", market: "IN", horizon: "short",
      signal: "HOLD", confidence: 55, current_price: 1307.8, target_price: 1283.0,
      equity_sell_suppressed: false,
      equity_sell_suppressed_note: null,
      reasoning: [], technical: { overall: "HOLD", rsi: 50, macd_diff: -0.1 },
      fundamental_score: { score: 50, reasons: [] },
      sentiment_score: { score: 50, label: "neutral", bullish: 1, bearish: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText((_, el) => el?.textContent === "— HOLD")).toBeInTheDocument());
    expect(screen.queryByText("Suppressed SELL")).not.toBeInTheDocument();
  });

  it("does NOT show the Suppressed SELL callout for a HOLD from a stale cache entry lacking the new fields", async () => {
    mockFetchQuote.mockResolvedValue(BASE_QUOTE);
    mockFetchPrediction.mockResolvedValue({
      symbol: "RELIANCE", market: "IN", horizon: "short",
      signal: "HOLD", confidence: 55, current_price: 1307.8, target_price: 1283.0,
      // No equity_sell_suppressed field at all — simulates a response
      // served from a pre-PR-#85 cache entry within the 15-min TTL window.
      reasoning: [], technical: { overall: "HOLD", rsi: 50, macd_diff: -0.1 },
      fundamental_score: { score: 50, reasons: [] },
      sentiment_score: { score: 50, label: "neutral", bullish: 1, bearish: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText((_, el) => el?.textContent === "— HOLD")).toBeInTheDocument());
    // Undefined must never be treated as "suppressed" — the page renders
    // fine and simply omits the callout, matching the optional-field contract.
    expect(screen.queryByText("Suppressed SELL")).not.toBeInTheDocument();
  });

  it("never shows the Suppressed SELL callout for a genuine BUY", async () => {
    mockFetchQuote.mockResolvedValue(BASE_QUOTE);
    mockFetchPrediction.mockResolvedValue({
      symbol: "RELIANCE", market: "IN", horizon: "short",
      signal: "BUY", confidence: 80, current_price: 1307.8, target_price: 1400.0,
      equity_sell_suppressed: false,
      equity_sell_suppressed_note: null,
      reasoning: [], technical: { overall: "BUY", rsi: 65, macd_diff: 0.5 },
      fundamental_score: { score: 70, reasons: [] },
      sentiment_score: { score: 60, label: "neutral", bullish: 3, bearish: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText((_, el) => el?.textContent === "▲ BUY")).toBeInTheDocument());
    expect(screen.queryByText("Suppressed SELL")).not.toBeInTheDocument();
  });
});
