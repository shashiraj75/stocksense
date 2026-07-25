import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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

const ORIGINAL_UI_FLAG = process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;

function restoreUiFlag() {
  if (ORIGINAL_UI_FLAG === undefined) {
    delete process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;
  } else {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = ORIGINAL_UI_FLAG;
  }
}

// Every test in the main describe block below exercises BACKEND-gated
// behavior (disabled response, market mismatch, rendered content, etc.) —
// those all require the frontend presentation gate itself to be open, so
// it's enabled by default here and the frontend-gate tests (further down)
// explicitly override it per case.
beforeEach(() => {
  process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
});

afterEach(() => {
  cleanup();
  mockGet.mockReset();
  restoreUiFlag();
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

const OK_RESPONSE_US = {
  ...OK_RESPONSE,
  market: "US",
  symbol: "AAPL",
  rs: { ...OK_RESPONSE.rs, market: "US", symbol: "AAPL" },
};

describe("MarketLeadershipContext (frontend gate enabled — backend-gated behavior)", () => {
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

  it("renders correctly for a valid IN result", async () => {
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText("Experimental")).toBeTruthy();
  });

  it("renders correctly for a valid US result", async () => {
    mockGet.mockResolvedValue({ data: OK_RESPONSE_US });
    renderWithClient(<MarketLeadershipContext symbol="AAPL" market="US" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText("Experimental")).toBeTruthy();
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

describe("MarketLeadershipContext — frontend presentation gate (pre-publication audit fix)", () => {
  // This block deliberately overrides the module-level beforeEach's
  // NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED="1" default per case, since
  // the whole point here is exercising the gate itself.

  it("issues zero API calls when the flag is absent", async () => {
    delete process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it('issues zero API calls when the flag is "0"', async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "0";
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it('issues zero API calls when the flag is "true"', async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "true";
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("issues zero API calls for malformed flag text", async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "enabled-please";
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await new Promise((r) => setTimeout(r, 50));
    expect(mockGet).not.toHaveBeenCalled();
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("shows no loading state while the frontend gate is closed", () => {
    delete process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED;
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    const { container } = renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    expect(container.textContent).toBe("");
  });

  it('permits the query when the flag is the exact string "1"', async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(mockGet).toHaveBeenCalledWith("/api/leadership/context?symbol=TCS&market=IN");
  });

  it("frontend enabled + backend disabled renders nothing", async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    mockGet.mockResolvedValue({ data: { status: "disabled" } });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });

  it("frontend enabled + valid IN result renders correctly", async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    mockGet.mockResolvedValue({ data: OK_RESPONSE });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText("Experimental")).toBeTruthy();
  });

  it("frontend enabled + valid US result renders correctly", async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    mockGet.mockResolvedValue({ data: OK_RESPONSE_US });
    renderWithClient(<MarketLeadershipContext symbol="AAPL" market="US" />);
    await waitFor(() => expect(screen.getByTestId("market-leadership-context")).toBeTruthy());
    expect(screen.getByText("Experimental")).toBeTruthy();
  });

  it("frontend enabled + market mismatch still renders nothing", async () => {
    process.env.NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED = "1";
    mockGet.mockResolvedValue({ data: { ...OK_RESPONSE, market: "US" } });
    renderWithClient(<MarketLeadershipContext symbol="TCS" market="IN" />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(screen.queryByTestId("market-leadership-context")).toBeNull();
  });
});
