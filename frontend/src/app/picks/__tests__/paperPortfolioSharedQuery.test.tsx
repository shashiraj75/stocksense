import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { useQuery, QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";

// Paper Trading Repeat-Buy Awareness — Phase 1 (Daily Picks Open-Trade
// Count). Proves the actually-testable page-level BEHAVIOR that
// openTradeCountPageWiring.test.ts locks in as source text: one shared
// `["paper-portfolio", userId]` query serves every `PickCard` (no N+1),
// unauthenticated users trigger zero fetches, loading/error states omit
// the badge (never a fabricated "0"), and the existing
// `invalidateQueries({queryKey: ["paper-portfolio"]})` call already made
// by PaperTradeModal.tsx on a successful Buy propagates to this
// subscription and updates the visible count — reusing the EXACT hook
// pattern page.tsx uses (query key, fetchFn, enabled gate) so this harness
// can't silently drift from the real wiring.

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: mockUser }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

const mockFetchQuote = vi.fn();
const mockFetchPaperPortfolio = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchQuote: (...args: unknown[]) => mockFetchQuote(...args),
    fetchPaperPortfolio: (...args: unknown[]) => mockFetchPaperPortfolio(...args),
  };
});

import type { Pick } from "@/app/picks/page";
import { buildOpenTradeCountMap, openTradeCountKey } from "@/utils/openTradeCount";
const { PickCard } = await import("@/app/picks/page");
const { fetchPaperPortfolio } = await import("@/utils/api");

let mockUser: { id: string; email: string } | null = { id: "user-1", email: "u@example.com" };

afterEach(() => {
  cleanup();
  mockFetchPaperPortfolio.mockReset();
  mockFetchQuote.mockReset();
  mockUser = { id: "user-1", email: "u@example.com" };
});

mockFetchQuote.mockResolvedValue({ price: 100, quote_price_basis: "current", quote_timestamp: "2026-08-07T00:00:00Z" });

function pick(symbol: string): Pick {
  return {
    symbol, name: symbol, price: 100, target: 120, stop_loss: 90,
    entry_low: 95, entry_high: 105, confidence: 70, fund_score: 60, sentiment: "BULLISH",
    reasoning: [{ indicator: "RSI", signal: "BUY", reason: "setup" }],
    horizon: "short", generated_at: "2026-08-06T00:00:00Z",
    generation_reference_price: 100, generation_reference_source: "close",
    generation_reference_price_basis: "close",
  };
}

// Mirrors the exact page-level hook usage in DailyPicksPage (picks/page.tsx):
// one `useQuery(["paper-portfolio", userId])` call, gated on `!!userId`,
// feeding `buildOpenTradeCountMap` and passing per-card counts down.
function PicksListHarness({ symbols }: { symbols: string[] }) {
  const userId = mockUser?.id ?? "";
  const { data: portfolioForCounts, isLoading, isError } = useQuery({
    queryKey: ["paper-portfolio", userId],
    queryFn: () => fetchPaperPortfolio(userId, mockUser?.email),
    enabled: !!userId,
  });
  const openTradeCountMap = portfolioForCounts ? buildOpenTradeCountMap(portfolioForCounts.open_trades) : null;
  return (
    <div>
      <div data-testid="loading">{String(isLoading)}</div>
      <div data-testid="error">{String(isError)}</div>
      {symbols.map((s, i) => (
        <PickCard
          key={s} pick={pick(s)} rank={i + 1} market="IN" currency="₹" locale="en-IN"
          openTradeCount={openTradeCountMap?.get(openTradeCountKey("IN", s))}
        />
      ))}
    </div>
  );
}

function TriggerInvalidation() {
  const queryClient = useQueryClient();
  return (
    <button onClick={() => queryClient.invalidateQueries({ queryKey: ["paper-portfolio"] })}>
      simulate-buy-success-invalidation
    </button>
  );
}

function renderHarness(symbols: string[], queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })) {
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TriggerInvalidation />
      <PicksListHarness symbols={symbols} />
    </QueryClientProvider>
  );
  return { ...utils, queryClient };
}

describe("Daily Picks shared paper-portfolio query", () => {
  it("issues exactly one portfolio fetch regardless of how many Daily Pick cards are rendered (no N+1)", async () => {
    mockFetchPaperPortfolio.mockResolvedValue({
      open_trades: [
        { id: 1, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100, exit_price: null, stop_loss: null, target_price: null, status: "OPEN", signal: "BUY", horizon: "short", opened_at: "2026-08-01T00:00:00Z", closed_at: null, invested: 100, trade_management_mode: "manual", exit_reason: null },
      ],
    });
    renderHarness(["TCS", "INFY", "RELIANCE", "HDFC", "WIPRO"]);
    await screen.findByText("1 open trade");
    expect(mockFetchPaperPortfolio).toHaveBeenCalledTimes(1);
  });

  it("unauthenticated (no userId) triggers zero portfolio fetch calls", async () => {
    mockUser = null;
    renderHarness(["TCS", "INFY"]);
    await waitFor(() => expect(screen.getByTestId("loading").textContent).toBe("false"));
    expect(mockFetchPaperPortfolio).not.toHaveBeenCalled();
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();
  });

  it("renders no badge (not a fabricated 0) while the portfolio query is loading", async () => {
    let resolveFetch: (v: unknown) => void = () => {};
    mockFetchPaperPortfolio.mockReturnValue(new Promise((resolve) => { resolveFetch = resolve; }));
    renderHarness(["TCS"]);
    expect(screen.getByTestId("loading").textContent).toBe("true");
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();
    resolveFetch({ open_trades: [] });
  });

  it("renders no badge on portfolio query error, and does not disable the Paper Trade button", async () => {
    mockFetchPaperPortfolio.mockRejectedValue(new Error("network error"));
    renderHarness(["TCS"]);
    await waitFor(() => expect(screen.getByTestId("error").textContent).toBe("true"));
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: /paper trade/i });
    expect(button).not.toBeDisabled();
  });

  it("a successful Buy's existing ['paper-portfolio'] prefix invalidation updates the visible count without any new invalidation mechanism", async () => {
    mockFetchPaperPortfolio
      .mockResolvedValueOnce({ open_trades: [] })
      .mockResolvedValueOnce({
        open_trades: [
          { id: 1, symbol: "TCS", market: "IN", quantity: 1, entry_price: 100, exit_price: null, stop_loss: null, target_price: null, status: "OPEN", signal: "BUY", horizon: "short", opened_at: "2026-08-01T00:00:00Z", closed_at: null, invested: 100, trade_management_mode: "manual", exit_reason: null },
        ],
      });
    renderHarness(["TCS"]);
    await waitFor(() => expect(mockFetchPaperPortfolio).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();

    screen.getByRole("button", { name: /simulate-buy-success-invalidation/i }).click();

    await screen.findByText("1 open trade");
    expect(mockFetchPaperPortfolio).toHaveBeenCalledTimes(2);
  });
});
