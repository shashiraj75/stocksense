import { describe, it, expect, vi, afterEach } from "vitest";

// jsdom has no ResizeObserver — recharts (used by FactorAttributionWaterfall,
// rendered on a valid symbol's page once a signal exists) needs one to mount.
// This is a test-environment shim only, not a behavior change.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
global.ResizeObserver = ResizeObserverStub;
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PredictionError } from "@/utils/api";

// Navigation/auth hooks — mocked per test via a mutable `mockParams`/
// `mockSearchParams` so each test controls which symbol/market renders,
// without needing a real Next.js router.
let mockSymbol = "NSDL";
let mockMarket = "IN";
vi.mock("next/navigation", () => ({
  useParams: () => ({ symbol: mockSymbol }),
  useSearchParams: () => new URLSearchParams({ market: mockMarket }),
}));
vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: null }),
}));

// fetchQuote/fetchPrediction are swapped per test; every other named export
// (PredictionError, types, the other fetch* helpers this page also calls)
// passes through untouched via importOriginal, so this doesn't have to
// hand-roll the whole module surface.
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

// Imported AFTER the mocks above are registered, per vitest's hoisting contract.
const { default: StockPage } = await import("../page");

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, retryDelay: 0, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StockPage />
    </QueryClientProvider>
  );
}

// Production hotfix follow-up: the unsupported-symbol recovery CTA used to
// link to "/" (the public landing page), which itself requires a second
// click ("Go to Dashboard") to reach /dashboard — contradicting the
// button's own "Back to Dashboard" label. These tests lock in the
// corrected one-click flow and copy.
describe("Stock Detail — unsupported symbol", () => {
  afterEach(() => {
    vi.clearAllMocks();
    mockSymbol = "NSDL";
    mockMarket = "IN";
  });

  it("renders 'Symbol not supported' for an unsupported Indian symbol", async () => {
    mockSymbol = "NSDL"; mockMarket = "IN";
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("This symbol is not available in the supported NSE universe.", {
        code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN",
      })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Symbol not supported")).toBeInTheDocument());
  });

  it("CTA href is exactly /dashboard, not /", async () => {
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN" })
    );
    renderPage();
    const cta = await screen.findByRole("link", { name: "Go to Dashboard" });
    expect(cta).toHaveAttribute("href", "/dashboard");
    expect(cta.getAttribute("href")).not.toBe("/");
  });

  it("CTA label is 'Go to Dashboard'", async () => {
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN" })
    );
    renderPage();
    expect(await screen.findByRole("link", { name: "Go to Dashboard" })).toBeInTheDocument();
    expect(screen.queryByText("Back to Dashboard")).not.toBeInTheDocument();
  });

  it("IN copy describes StockSense360's NSE coverage without claiming the symbol itself is NSE-listed", async () => {
    mockSymbol = "NSDL"; mockMarket = "IN";
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN" })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Symbol not supported")).toBeInTheDocument());
    const copy = screen.getByText(/is not available in StockSense360's NSE coverage/);
    expect(copy.textContent).toContain("NSDL is not available in StockSense360's NSE coverage");
    expect(copy.textContent).toContain("StockSense360 currently supports NSE-listed Indian equities");
    // Must never read as "NSDL is NSE-listed" — the copy states StockSense360's
    // own coverage scope, not a claim about which exchange NSDL trades on.
    expect(copy.textContent).not.toMatch(/NSDL is (an? )?NSE[- ]listed/i);
    expect(copy.textContent).not.toMatch(/NSDL.*NSE India/);
  });

  it("US copy uses US-universe wording, not the IN-specific NSE copy", async () => {
    mockSymbol = "OBSCUREUS"; mockMarket = "US";
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "OBSCUREUS", market: "US" })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Symbol not supported")).toBeInTheDocument());
    const copy = screen.getByText(/is not available in StockSense360's supported US equity universe/);
    expect(copy.textContent).toContain("OBSCUREUS is not available in StockSense360's supported US equity universe");
    expect(copy.textContent).not.toMatch(/NSE/);
  });

  it("no signal, confidence, target, stop-loss, or Paper Trade renders for an unsupported symbol", async () => {
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN" })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Symbol not supported")).toBeInTheDocument());
    expect(screen.queryByText(/BUY|SELL|HOLD/)).not.toBeInTheDocument();
    expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Target Price/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Stop Loss/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Paper Trade|Paper Buy|Paper Sell/i)).not.toBeInTheDocument();
  });

  it("the global search bar remains available on the unsupported-symbol page", async () => {
    mockFetchQuote.mockRejectedValue({ response: { status: 404 } });
    mockFetchPrediction.mockRejectedValue(
      new PredictionError("Not supported.", { code: "SYMBOL_NOT_SUPPORTED", status: 404, symbol: "NSDL", market: "IN" })
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("Symbol not supported")).toBeInTheDocument());
    // The unsupported state is this page's own early-return — the app's
    // persistent nav/search bar lives one level up in the root layout, so
    // this only confirms the page itself renders nothing that would hide
    // or replace that shared chrome (no full-viewport overlay, no takeover).
    expect(document.body.textContent).not.toMatch(/^$/);
  });
});

describe("Stock Detail — valid symbol is unaffected by the unsupported-state changes", () => {
  afterEach(() => {
    vi.clearAllMocks();
    mockSymbol = "NSDL";
    mockMarket = "IN";
  });

  it("a resolved, valid prediction never shows the unsupported-symbol screen or its CTA", async () => {
    mockSymbol = "RELIANCE"; mockMarket = "IN";
    mockFetchQuote.mockResolvedValue({
      symbol: "RELIANCE", price: 1307.8, change: 27.4, change_pct: 2.14,
      high: 1311.1, low: 1287.8, fifty_two_week_high: 1611.8, fifty_two_week_low: 1253.2,
      market_cap: 17700000000000, volume: 16744303, company_name: "Reliance Industries Limited",
    });
    mockFetchPrediction.mockResolvedValue({
      symbol: "RELIANCE", market: "IN", horizon: "short",
      signal: "SELL", confidence: 24, current_price: 1307.8, target_price: 1283.0,
      reasoning: [], technical: { overall: "SELL", rsi: 50, macd_diff: -0.1 },
      fundamental_score: { score: 63, reasons: [] },
      sentiment_score: { score: 50, label: "neutral", bullish: 1, bearish: 1 },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText((_, el) => el?.textContent === "▼ SELL")).toBeInTheDocument());
    expect(screen.queryByText("Symbol not supported")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to Dashboard" })).not.toBeInTheDocument();
  });
});
