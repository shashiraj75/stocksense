import { describe, it, expect, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// V-SEC2 — removes the public-facing production-job trigger from
// /validation. The manual HTTP endpoint (POST /api/validation/run) is now
// X-Secret-protected (V-SEC1); this page must never be able to call it,
// and the secret must never appear in any browser-reachable code.
//
// Combines a source-level audit (the strongest guarantee no reachable code
// path — even one not exercised by the specific render below — still
// calls the endpoint) with behavioral component tests (proving the page
// actually renders and functions correctly without the removed control).

const PAGE_PATH = path.resolve(process.cwd(), "src/app/validation/page.tsx");
const pageSource = readFileSync(PAGE_PATH, "utf-8");

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/utils/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

const { default: ValidationPage } = await import("../page");

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ValidationPage />
    </QueryClientProvider>,
  );
}

const RESULTS = {
  available: true,
  horizon: "medium",
  n_stocks_tested: 3,
  run_at: "2026-08-11T00:35:24.157619+00:00",
  total_signals: 100,
  buy_signals: 40,
  sell_signals: 30,
  overall_accuracy_pct: 49.2,
  buy_hit_rate_pct: 53.7,
  sell_hit_rate_pct: 44.4,
  avg_return_on_buy_pct: 2.27,
  avg_alpha_on_buy_pct: 1.53,
  profitable_buy_pct: 57.9,
  beat_benchmark_pct: 48.0,
  sharpe_on_alphas: 0.63,
  nifty_avg_fwd_return_pct: 0.66,
  score_buckets: [],
  factor_ic: { tech: 0.01, rs: 0.02, obv: 0.0, mfi: 0.01, composite: 0.01 },
};

function mockApi({ results = RESULTS, running = false }: { results?: object | null; running?: boolean } = {}) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/results/stocks")) {
      return Promise.resolve({ data: { available: true, stocks: [] } });
    }
    if (url.includes("/results?")) {
      return Promise.resolve({ data: results ?? { available: false } });
    }
    if (url.includes("/status")) {
      return Promise.resolve({
        data: { running, progress: running ? 12 : 0, total: running ? 134 : 0, started_at: null, log: [] },
      });
    }
    return Promise.resolve({ data: {} });
  });
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

// ─────────────────────────────────────────────────────────────────────────
// 1-3, 10: source-level audit — no reachable code path can POST /run
// ─────────────────────────────────────────────────────────────────────────
describe("source audit — no browser path can trigger validation", () => {
  it("contains no 'Run Now' control text anywhere", () => {
    expect(pageSource).not.toMatch(/Run Now/i);
  });

  it("never references the POST /api/validation/run endpoint", () => {
    expect(pageSource).not.toContain("/api/validation/run");
  });

  it("does not import or use useMutation (the only thing that ever called the trigger)", () => {
    expect(pageSource).not.toContain("useMutation");
  });

  it("does not import the Play icon (only ever used by the removed button)", () => {
    expect(pageSource).not.toMatch(/\bPlay\b/);
  });

  it("contains no PICKS_SECRET or X-Secret reference", () => {
    expect(pageSource).not.toContain("PICKS_SECRET");
    expect(pageSource).not.toMatch(/X-Secret/i);
  });

  it("api.post is never called anywhere in the module (only api.get is used)", () => {
    // A stronger guarantee than searching for the specific URL string —
    // proves no POST call of any kind survives in this file, so a renamed
    // or refactored trigger couldn't slip back in unnoticed.
    expect(pageSource).not.toMatch(/api\.post/);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 1, 2, 9: rendered page has no interactive trigger control
// ─────────────────────────────────────────────────────────────────────────
describe("rendered page — no user control can trigger validation", () => {
  it("renders with results and shows no 'Run Now' button", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.queryByRole("button", { name: /run now/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/run now/i)).not.toBeInTheDocument();
  });

  it("renders the empty state without any 'Run Now' prompt", async () => {
    mockApi({ results: { available: false } });
    renderPage();
    await screen.findByText(/No validation results yet/i);
    expect(screen.queryByText(/run now/i)).not.toBeInTheDocument();
    expect(screen.getByText(/next automatic validation run/i)).toBeInTheDocument();
  });

  it("no button click anywhere on the page ever issues a POST request", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    const buttons = screen.getAllByRole("button");
    for (const btn of buttons) {
      fireEvent.click(btn);
    }
    await new Promise((r) => setTimeout(r, 0));
    expect(mockPost).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 4, 5: existing GET requests / selectors still work
// ─────────────────────────────────────────────────────────────────────────
describe("results still load and selectors still refetch", () => {
  it("fetches results via GET on initial render", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(mockGet).toHaveBeenCalledWith(expect.stringContaining("/api/validation/results?horizon=medium&universe=nifty100"));
  });

  it("switching universe triggers a fresh GET for the new universe", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    mockGet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /US.*S&P 500 basket/i }));
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(expect.stringContaining("universe=us"));
    });
  });

  it("switching horizon triggers a fresh GET for the new horizon", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    mockGet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /^Short/i }));
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith(expect.stringContaining("horizon=short"));
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 6: the retained "Refresh displayed results" control is GET-only
// ─────────────────────────────────────────────────────────────────────────
describe("Refresh displayed results control", () => {
  it("is labeled truthfully and performs only a GET refetch, never a POST", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    const refreshBtn = screen.getByRole("button", { name: /refresh displayed results/i });
    mockGet.mockClear();
    mockPost.mockClear();
    fireEvent.click(refreshBtn);
    await waitFor(() => {
      expect(mockGet).toHaveBeenCalled();
    });
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("source shows the refresh control only invalidates query caches (GET refetch), never posts", () => {
    const refreshBlockMatch = pageSource.match(/Refresh displayed results[\s\S]{0,400}/);
    expect(refreshBlockMatch).not.toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 7, 8: accurate completion-time wording, not a freshness/market-data claim
// ─────────────────────────────────────────────────────────────────────────
describe("timestamp wording", () => {
  it("labels run_at as 'Validation completed', not 'Last run' or a data-freshness claim", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Validation completed/i);
    expect(screen.queryByText(/^Last run/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/market data updated/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/data current through/i)).not.toBeInTheDocument();
  });

  it("renders the correct completion timestamp value", async () => {
    mockApi();
    renderPage();
    const label = await screen.findByText(/Validation completed/i);
    // IST-formatted rendering of 2026-08-11T00:35:24Z — just assert the
    // strong value node is non-empty and not the placeholder dash.
    const strong = label.closest("span")?.querySelector("strong");
    expect(strong?.textContent).toBeTruthy();
    expect(strong?.textContent).not.toBe("—");
  });

  it("discloses this is the last successful completion, not guaranteed market-data freshness", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/last successful validation completion/i);
  });

  it("source never labels run_at with an unproven freshness claim", () => {
    expect(pageSource).not.toMatch(/Market data updated/i);
    expect(pageSource).not.toMatch(/Data current through/i);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 9: loading / error / empty states remain functional
// ─────────────────────────────────────────────────────────────────────────
describe("loading, error and empty states", () => {
  it("shows the loading indicator before results resolve", () => {
    mockGet.mockImplementation(() => new Promise(() => {})); // never resolves
    renderPage();
    expect(screen.getByText(/Loading results/i)).toBeInTheDocument();
  });

  it("shows the empty state when no results are available", async () => {
    mockApi({ results: { available: false } });
    renderPage();
    await screen.findByText(/No validation results yet/i);
  });

  it("renders successfully with a full results payload (success state)", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.getByText(/Overall Accuracy/i)).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Read-only progress indicator for an already-running scheduled job must
// remain purely informational — no click target, no mutation call.
// ─────────────────────────────────────────────────────────────────────────
describe("running-job indicator stays read-only", () => {
  it("shows a non-interactive progress indicator, not a button, while a matching job runs", async () => {
    mockApi({ running: true });
    renderPage();
    await screen.findByText(/Validation running/i);
    expect(screen.queryByRole("button", { name: /validation running/i })).not.toBeInTheDocument();
  });
});
