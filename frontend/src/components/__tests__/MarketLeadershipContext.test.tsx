import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockGet = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => mockGet(...args) } };
});

const { MarketLeadershipContext } = await import("../MarketLeadershipContext");

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  mockGet.mockReset();
});

const OK_RESPONSE = {
  status: "ok",
  market: "IN",
  symbol: "TCS",
  as_of: "2026-07-20",
  rs: {
    symbol: "TCS", market: "IN", stock_rs_score: 82, stock_rs_percentile: 82,
    rs_trend: "IMPROVING", data_quality_status: "OK", methodology_version: "ml-rs-1.0.0",
  },
  trend: {
    state: "CONFIRMED_ADVANCE", extension_risk: "MODERATE", evidence_completeness: 0.9,
    methodology_version: "ml-tl-1.0.0",
  },
  why_now: {
    summary: "Stock RS is 82 and improving.", supporting_factors: ["Stock RS is 82 and improving."],
    caution_factors: ["Price is extended above its medium-term trend."],
    data_freshness: "current", data_quality: "OK", methodology_version: "ml-ex-1.0.0",
  },
};

describe("MarketLeadershipContext", () => {
  it("renders nothing when the backend returns disabled", async () => {
    mockGet.mockResolvedValue({ data: { status: "disabled" } });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("renders nothing on a fetch error (never an error banner)", async () => {
    mockGet.mockRejectedValue(new Error("network down"));
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("renders the panel with the Experimental label when data is fully formed", async () => {
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText("Experimental")).toBeTruthy();
    expect(screen.getByText("82")).toBeTruthy();
    expect(screen.getByText("Confirmed Advance")).toBeTruthy();
  });

  it("shows caution factors when present", async () => {
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText(/extended above its medium-term trend/)).toBeTruthy();
  });

  it("never renders context for a mismatched market (belt-and-suspenders isolation)", async () => {
    mockGet.mockResolvedValue({ data: { ...OK_RESPONSE, market: "US" } });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("requests the exact symbol and market via the API", async () => {
    mockGet.mockResolvedValue({ data: { status: "disabled" } });
    renderWithClient(<MarketLeadershipContext symbol="AAPL" market="US" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(mockGet).toHaveBeenCalledWith("/api/leadership/context?symbol=AAPL&market=US");
  });

  it("never presents itself as a BUY/HOLD/SELL signal — only a disclosure separating it from one", async () => {
    // Section 14 #6/#7: keep BUY/HOLD/SELL separate from trend lifecycle,
    // never imply high RS/trend state means BUY. The component must not
    // render its own signal badge — the only permitted mention of
    // BUY/SELL is the explicit "separate from the signal above" disclosure.
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    const container = screen.getByTestId("market-leadership-context");
    expect(container.textContent).toMatch(/not a recommendation/i);
    expect(container.textContent).toMatch(/separate from the BUY\/HOLD\/SELL signal above/i);
    // No standalone recommendation badge (e.g. a bare "BUY" pill) exists.
    expect(screen.queryByText(/^BUY$/)).toBeNull();
    expect(screen.queryByText(/^SELL$/)).toBeNull();
  });
});
