import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// PR #32 pre-merge correction — proves the disabled/enabled behavior at
// the rendered-component level, not just via source-scanning: when
// disabled, the page must render a safe unavailable state and must NEVER
// call fetchDailyPostmortem; when enabled, the normal Sprint 1 UI (date
// picker, market toggle) renders as before.
vi.mock("@/lib/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-aaa" } }),
}));

const mockFetchDailyPostmortem = vi.fn();
vi.mock("@/utils/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/utils/api")>();
  return {
    ...actual,
    fetchDailyPostmortem: (...args: unknown[]) => mockFetchDailyPostmortem(...args),
  };
});

function renderPage(PageComponent: React.ComponentType) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <PageComponent />
    </QueryClientProvider>
  );
}

const ORIGINAL_FLAG = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;

beforeEach(() => {
  mockFetchDailyPostmortem.mockReset();
  mockFetchDailyPostmortem.mockResolvedValue({
    schema_version: "2.0.0", calculation_version: "2.0.0", warnings: [],
    summary: {
      date: "2026-06-02", market: "ALL", trade_count: 0, win_count: 0, loss_count: 0,
      breakeven_count: 0, indeterminate_count: 0, total_realized_pnl_abs: null,
      pnl_excluded_trade_count: 0, recurring_supported_contributors: {},
      recurring_conflicting_contributors: {}, recurring_not_assessable_count: {},
      trades_with_no_supported_contributor: 0,
    },
    trades: [],
  });
});

afterEach(() => {
  cleanup();
  vi.resetModules();
  if (ORIGINAL_FLAG === undefined) delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
  else process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = ORIGINAL_FLAG;
});

describe("PostmortemPage — disabled by default", () => {
  it("renders a safe unavailable state and never calls fetchDailyPostmortem", async () => {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
    const { default: PostmortemPage } = await import("../page");
    renderPage(PostmortemPage);

    expect(screen.getByText(/postmortem reports aren.t available yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/daily trade postmortem report/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/date/i, { selector: "input" })).not.toBeInTheDocument();
    expect(mockFetchDailyPostmortem).not.toHaveBeenCalled();
  });

  it("also stays unavailable for an explicit false value", async () => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = "false";
    const { default: PostmortemPage } = await import("../page");
    renderPage(PostmortemPage);

    expect(screen.getByText(/postmortem reports aren.t available yet/i)).toBeInTheDocument();
    expect(mockFetchDailyPostmortem).not.toHaveBeenCalled();
  });

  it("also stays unavailable for an invalid value", async () => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = "maybe-later";
    const { default: PostmortemPage } = await import("../page");
    renderPage(PostmortemPage);

    expect(screen.getByText(/postmortem reports aren.t available yet/i)).toBeInTheDocument();
    expect(mockFetchDailyPostmortem).not.toHaveBeenCalled();
  });
});

describe("PostmortemPage — explicitly enabled", () => {
  it("renders the full Sprint 1 UI and fetches the daily report", async () => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = "true";
    const { default: PostmortemPage } = await import("../page");
    renderPage(PostmortemPage);

    expect(screen.getByText(/daily trade postmortem report/i)).toBeInTheDocument();
    expect(screen.queryByText(/postmortem reports aren.t available yet/i)).not.toBeInTheDocument();
    expect(mockFetchDailyPostmortem).toHaveBeenCalled();
  });
});
