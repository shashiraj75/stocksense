import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Paper Trading Repeat-Buy Awareness — Phase 1 (Daily Picks Open-Trade
// Count). UX coverage for the `openTradeCount` prop on `PickCard`
// (frontend/src/app/picks/page.tsx): badge presence/absence, singular vs
// plural text, and confirmation the Paper Trade button's existing
// stale-pick disable behavior is completely unaffected by this prop.

vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1", email: "u@example.com" } }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}));

vi.mock("@/utils/marketHours", () => ({
  getMarketStatus: () => ({ isOpen: true, label: "Market Open", nextEventLabel: null }),
}));

const mockFetchQuote = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchQuote: (...args: unknown[]) => mockFetchQuote(...args),
    fetchPaperPortfolio: vi.fn().mockResolvedValue({ open_trades: [] }),
  };
});

import type { Pick } from "@/app/picks/page";
const { PickCard } = await import("@/app/picks/page");

afterEach(() => cleanup());

mockFetchQuote.mockResolvedValue({ price: 100, quote_price_basis: "current", quote_timestamp: "2026-08-07T00:00:00Z" });

const basePick: Pick = {
  symbol: "TCS", name: "Tata Consultancy", price: 100, target: 120, stop_loss: 90,
  entry_low: 95, entry_high: 105, confidence: 70, fund_score: 60, sentiment: "BULLISH",
  reasoning: [{ indicator: "RSI", signal: "BUY", reason: "setup" }],
  horizon: "short", generated_at: "2026-08-06T00:00:00Z",
  generation_reference_price: 100, generation_reference_source: "close",
  generation_reference_price_basis: "close",
};

function renderCard(overrides: Partial<React.ComponentProps<typeof PickCard>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PickCard pick={basePick} rank={1} market="IN" currency="₹" locale="en-IN" {...overrides} />
    </QueryClientProvider>
  );
}

describe("PickCard open-trade count badge", () => {
  it("renders no badge when openTradeCount is undefined (e.g. loading/error/unauthenticated upstream)", () => {
    renderCard({ openTradeCount: undefined });
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();
  });

  it("renders no badge when openTradeCount is 0 — not even a '0 open trades' badge", () => {
    renderCard({ openTradeCount: 0 });
    expect(screen.queryByText(/open trade/i)).not.toBeInTheDocument();
  });

  it("renders singular '1 open trade' for a count of 1", () => {
    renderCard({ openTradeCount: 1 });
    expect(screen.getByText("1 open trade")).toBeInTheDocument();
  });

  it("renders plural 'N open trades' for counts >= 2", () => {
    renderCard({ openTradeCount: 2 });
    expect(screen.getByText("2 open trades")).toBeInTheDocument();
    cleanup();
    renderCard({ openTradeCount: 5 });
    expect(screen.getByText("5 open trades")).toBeInTheDocument();
  });

  it("badge text is present as accessible text content, not just a visual artifact", () => {
    renderCard({ openTradeCount: 3 });
    const badge = screen.getByText("3 open trades");
    expect(badge.textContent).toBe("3 open trades");
  });

  it("never uses warning/alarm styling (no red/warning classes) for the badge", () => {
    renderCard({ openTradeCount: 4 });
    const badge = screen.getByText("4 open trades");
    expect(badge.className).not.toMatch(/red-/);
    expect(badge.className).not.toMatch(/yellow-/);
  });

  it("the Paper Trade button remains enabled regardless of openTradeCount when the pick is fresh", () => {
    renderCard({ openTradeCount: 7 });
    const button = screen.getByRole("button", { name: /paper trade/i });
    expect(button).not.toBeDisabled();
  });

  it("the Paper Trade button remains disabled for a stale/unknown pick regardless of openTradeCount — existing staleness disable logic is unchanged", () => {
    renderCard({
      openTradeCount: 7,
      freshness: { freshnessStatus: "stale", referenceSessionDate: "2026-08-05" } as any,
    });
    const button = screen.getByRole("button", { name: /paper trade/i });
    expect(button).toBeDisabled();
  });
});
