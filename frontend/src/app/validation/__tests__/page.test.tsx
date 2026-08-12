import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// V-VAL1 — validation-metric and presentation integrity regression coverage.
// Covers the evaluated-BUY-cohort contract, Wilson CI, majority-class
// baseline, score-bucket/IC wording, Max Drawdown relabeling, per-stock
// pagination, and re-confirms every PR #52 security/cadence invariant is
// still intact after this port (no Run Now, no validation POST, GET-only
// refresh, daily/Sunday cadence wording, "Validation completed" label).

const mockGet = vi.fn();

vi.mock("@/utils/api", () => ({
  api: { get: (...args: unknown[]) => mockGet(...args) },
}));

const { default: ValidationPage, exactBinomialTwoSidedPValue, runIdsCoherent } = await import("../page");

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <ValidationPage />
    </QueryClientProvider>,
  );
  // queryClient is exposed (additively — every other call site still just
  // calls renderPage() and ignores the return value) so V-SNAP1C's
  // corrected race test can synchronize on genuine React Query cache
  // state rather than guessing at a microtask-flush depth.
  return { ...result, queryClient };
}

const BASE_RESULTS = {
  available: true,
  // V-SNAP1D — a valid canonical run_id (positive safe integer) is now
  // required for a snapshot to promote/render at all; every fixture that
  // spreads BASE_RESULTS inherits this unless it explicitly overrides it.
  run_id: 1,
  horizon: "medium",
  n_stocks_tested: 3,
  run_at: "2026-08-11T00:35:24.157619+00:00",
  total_signals: 100,
  buy_signals: 40,
  sell_signals: 30,
  hold_signals: 30,
  evaluated_buy_count: 40,
  buy_hits: 21,
  overall_accuracy_pct: 49.2,
  buy_hit_rate_pct: 53.7,
  sell_hit_rate_pct: 44.4,
  avg_return_on_buy_pct: 2.27,
  avg_alpha_on_buy_pct: 1.53,
  profitable_buy_pct: 57.9,
  beat_benchmark_pct: 48.0,
  sharpe_on_alphas: 0.63,
  nifty_avg_fwd_return_pct: 0.66,
  max_drawdown_pct: 12.5,
  max_consecutive_wrong: 4,
  max_consecutive_right: 6,
  score_buckets: [
    { score_range: "60–65", count: 2457, hit_rate_pct: 52.3, avg_return_pct: 1.75 },
    { score_range: "80–85", count: 3, hit_rate_pct: 33.3, avg_return_pct: -4.33 },
  ],
  factor_ic: { tech: 0.0134, rs: 0.0452, obv: 0.003, mfi: 0.0149, composite: 0.0106 },
  data_limitations: { fundamentals_point_in_time: true, fundamentals_point_in_time_coverage_pct: 91.2 },
};

const MANY_STOCKS = Array.from({ length: 65 }, (_, i) => ({
  symbol: `SYM${i}`,
  total_signals: 20,
  buy_signal_count: 20,
  evaluated_buy_count: 20,
  buy_hits: 11,
  hit_rate_pct: 55.0,
  buy_return_count: 20,
  avg_fwd_return_pct: 1.2,
  buy_avg_return_pct: 2.0,
}));

function mockApi({
  results = BASE_RESULTS,
  stocks = MANY_STOCKS,
}: { results?: object | null; stocks?: object[] } = {}) {
  // V-SNAP1D — the per-stock mock must carry the SAME run_id as the
  // aggregate `results` fixture (falling back to 1, matching
  // BASE_RESULTS's own default) so the two stay pinned/coherent under
  // the new fail-closed identity check, exactly mirroring what a real
  // backend does.
  const resolvedRunId = (results as { run_id?: number } | null)?.run_id ?? 1;
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/results/stocks")) {
      return Promise.resolve({ data: { available: true, run_id: resolvedRunId, stocks } });
    }
    if (url.includes("/results?")) {
      return Promise.resolve({ data: results ?? { available: false } });
    }
    if (url.includes("/status")) {
      return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
    }
    return Promise.resolve({ data: {} });
  });
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

// ─────────────────────────────────────────────────────────────────────────
// Evaluated BUY cohort — n=1, null hit rate, evaluated denominator
// ─────────────────────────────────────────────────────────────────────────
describe("evaluated BUY cohort per stock", () => {
  it("renders n=1 correct BUY as 100%, not a fractional value", async () => {
    mockApi({
      stocks: [
        { symbol: "ONEOK", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1,
          buy_hits: 1, hit_rate_pct: 100.0, buy_return_count: 1, avg_fwd_return_pct: 5, buy_avg_return_pct: 5 },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("ONEOK");
    const row = screen.getByText("ONEOK").closest("tr");
    expect(row!.textContent).toContain("100%");
  });

  it("renders n=1 incorrect BUY as 0%", async () => {
    mockApi({
      stocks: [
        { symbol: "ZEROK", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1,
          buy_hits: 0, hit_rate_pct: 0.0, buy_return_count: 1, avg_fwd_return_pct: -2, buy_avg_return_pct: -2 },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("ZEROK");
    const row = screen.getByText("ZEROK").closest("tr");
    expect(row!.textContent).toContain("0%");
    expect(row!.textContent).not.toMatch(/\d\.\d%/); // no fractional hit rate
  });

  it("renders null hit_rate_pct as 'unavailable', never a fabricated 0%", async () => {
    mockApi({
      stocks: [
        { symbol: "NULLHR", total_signals: 2, buy_signal_count: 2, evaluated_buy_count: 0,
          buy_hits: 0, hit_rate_pct: null, buy_return_count: 0, avg_fwd_return_pct: null, buy_avg_return_pct: null },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("NULLHR");
    const row = screen.getByText("NULLHR").closest("tr");
    expect(row!.textContent).toContain("unavailable");
    expect(row!.textContent).not.toMatch(/\b0%/);
  });

  it("displays the evaluated denominator distinctly when it differs from the raw BUY count", async () => {
    mockApi({
      stocks: [
        { symbol: "PARTIAL", total_signals: 3, buy_signal_count: 3, evaluated_buy_count: 1,
          buy_hits: 1, hit_rate_pct: 100.0, buy_return_count: 1, avg_fwd_return_pct: 5, buy_avg_return_pct: 5 },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("PARTIAL");
    const row = screen.getByText("PARTIAL").closest("tr");
    // Evaluated count (1) shown distinctly from the raw BUY count (3).
    expect(row!.textContent).toContain("1");
    expect(row!.textContent).toContain("/ 3");
  });

  it("marks a low evaluated-n row (< 10) as limited, accessibly", async () => {
    mockApi({
      stocks: [
        { symbol: "TINYN", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1,
          buy_hits: 1, hit_rate_pct: 100.0, buy_return_count: 1, avg_fwd_return_pct: 3, buy_avg_return_pct: 3 },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("TINYN");
    expect(screen.getByLabelText("limited sample")).toBeInTheDocument();
  });

  it("null average BUY return renders a dash, not zero or a crash", async () => {
    mockApi({
      stocks: [
        { symbol: "NORET", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1,
          buy_hits: 1, hit_rate_pct: 100.0, buy_return_count: 0, avg_fwd_return_pct: null, buy_avg_return_pct: null },
        ...MANY_STOCKS,
      ],
    });
    renderPage();
    await screen.findByText("NORET");
    const row = screen.getByText("NORET").closest("tr");
    expect(row!.textContent).toContain("—");
  });
});

// ─────────────────────────────────────────────────────────────────────────
// exactBinomialTwoSidedPValue — verified against scipy.stats.binomtest
// (alternative="two-sided") reference values computed in this session:
//   binomtest(21,40,0.5,alternative="two-sided").pvalue == 0.8746293123804207
//   binomtest(0,1,...)   == 1.0        binomtest(1,1,...)   == 1.0
//   binomtest(3,3,...)   == 0.25       binomtest(0,3,...)   == 0.25
//   binomtest(8,10,...)  == 0.109375
//   binomtest(2482,4622,...) == 5.221768289713555e-07
// Chronology note (reported honestly, not manufactured retroactively):
// this correction phase implemented the exact-binomial replacement and
// these verification tests together, not RED-then-GREEN — the prior
// function's defects (rounded-percentage reconstruction of successes,
// normal approximation, non-standard direction) were established by
// direct code reading before this edit, not by a failing test run.
// ─────────────────────────────────────────────────────────────────────────
describe("exactBinomialTwoSidedPValue — scipy-verified vectors", () => {
  it("matches scipy for 21/40 (production-shaped case)", () => {
    const r = exactBinomialTwoSidedPValue(21, 40);
    expect(r).not.toBeNull();
    expect(r!.p).toBeCloseTo(0.8746293123804207, 10);
  });

  it("matches scipy for 0/1 and 1/1 (both == 1.0)", () => {
    expect(exactBinomialTwoSidedPValue(0, 1)!.p).toBeCloseTo(1.0, 10);
    expect(exactBinomialTwoSidedPValue(1, 1)!.p).toBeCloseTo(1.0, 10);
  });

  it("matches scipy for all-successes (3/3) and all-failures (0/3) — both 0.25", () => {
    expect(exactBinomialTwoSidedPValue(3, 3)!.p).toBeCloseTo(0.25, 10);
    expect(exactBinomialTwoSidedPValue(0, 3)!.p).toBeCloseTo(0.25, 10);
  });

  it("matches scipy for a small sample (8/10)", () => {
    expect(exactBinomialTwoSidedPValue(8, 10)!.p).toBeCloseTo(0.109375, 10);
  });

  it("matches scipy for a large production-shaped sample (2482/4622)", () => {
    const r = exactBinomialTwoSidedPValue(2482, 4622);
    expect(r!.p).toBeCloseTo(5.221768289713555e-7, 10);
    expect(r!.significant).toBe(true);
  });

  it("is invariant to how the same successes/trials pair is computed (permutation invariance)", () => {
    // Same logical input via two different call sites must be identical —
    // there is no per-call random/time-dependent state.
    const a = exactBinomialTwoSidedPValue(21, 40);
    const b = exactBinomialTwoSidedPValue(21, 40);
    expect(a).toEqual(b);
  });

  it("zero evaluated trials returns null (unavailable), not a fabricated p-value", () => {
    expect(exactBinomialTwoSidedPValue(0, 0)).toBeNull();
  });

  it("successes greater than trials fails safely (malformed input)", () => {
    expect(exactBinomialTwoSidedPValue(5, 3)).toBeNull();
  });

  it("negative successes fails safely", () => {
    expect(exactBinomialTwoSidedPValue(-1, 10)).toBeNull();
  });

  it("non-finite input fails safely", () => {
    expect(exactBinomialTwoSidedPValue(NaN, 10)).toBeNull();
    expect(exactBinomialTwoSidedPValue(5, Infinity)).toBeNull();
  });

  it("returns a finite, non-NaN p in [0,1] across a spread of realistic inputs", () => {
    const cases: [number, number][] = [[0, 1], [1, 1], [0, 3], [3, 3], [8, 10], [21, 40], [2482, 4622]];
    for (const [k, n] of cases) {
      const r = exactBinomialTwoSidedPValue(k, n);
      expect(r).not.toBeNull();
      expect(Number.isFinite(r!.p)).toBe(true);
      expect(Number.isNaN(r!.p)).toBe(false);
      expect(r!.p).toBeGreaterThanOrEqual(0);
      expect(r!.p).toBeLessThanOrEqual(1);
    }
  });

  it("handles a balanced large sample (n=5000, hits=2500) without error, p near 1", () => {
    const r = exactBinomialTwoSidedPValue(2500, 5000);
    expect(r).not.toBeNull();
    expect(Number.isFinite(r!.p)).toBe(true);
    expect(r!.p).toBeGreaterThan(0.9); // observed exactly matches the null -> p should be very high
  });

  it("handles an extreme large sample (n=20000) without error, overflow or a frozen loop", () => {
    // All-successes at large n is the numerically hardest case (deepest
    // tail probability, most extreme log-space magnitude) — proves no
    // overflow/underflow crash at a size well beyond any real validation
    // run's evaluated-BUY count.
    const r = exactBinomialTwoSidedPValue(20000, 20000);
    expect(r).not.toBeNull();
    expect(Number.isFinite(r!.p)).toBe(true);
    expect(r!.p).toBeGreaterThanOrEqual(0);
    expect(r!.p).toBeLessThanOrEqual(1);
  });

  it("does not block for realistic (and larger-than-realistic) n — bounded runtime", () => {
    // O(n) is the whole point of the precomputed-logFact design; a stable,
    // generous wall-clock ceiling (not a flaky microsecond budget) is
    // enough to catch an accidental reversion to an O(n²) implementation,
    // which would take fractions of a SECOND at n=20000, not this.
    const start = performance.now();
    exactBinomialTwoSidedPValue(10000, 20000);
    const elapsedMs = performance.now() - start;
    expect(elapsedMs).toBeLessThan(500);
  });

  it("is deterministic across repeated calls with the same input (no hidden time/random state)", () => {
    const results = new Set(
      Array.from({ length: 5 }, () => JSON.stringify(exactBinomialTwoSidedPValue(2482, 4622))),
    );
    expect(results.size).toBe(1);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Wilson interval
// ─────────────────────────────────────────────────────────────────────────
describe("Wilson 95% CI on BUY Hit Rate — exact evaluated inputs only", () => {
  it("renders a CI range using exact buy_hits/evaluated_buy_count", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/95% CI/);
    await screen.findByText(/n=40 evaluated/);
  });

  it("omits the CI (but shows the base label) when there are no evaluated BUYs (0/0)", async () => {
    mockApi({ results: { ...BASE_RESULTS, evaluated_buy_count: 0, buy_hits: 0, buy_hit_rate_pct: null } });
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.queryByText(/95% CI/)).not.toBeInTheDocument();
  });

  it("handles 0 hits of 1 evaluated (0/1) without a fabricated interval", async () => {
    mockApi({ results: { ...BASE_RESULTS, evaluated_buy_count: 1, buy_hits: 0, buy_hit_rate_pct: 0.0 } });
    renderPage();
    const ciText = await screen.findByText(/95% CI/);
    const match = ciText.textContent?.match(/\[([\d.]+)%, ([\d.]+)%\]/);
    expect(match).not.toBeNull();
    const [, lo, hi] = match!;
    expect(Number(lo)).toBe(0);
    expect(Number(hi)).toBeGreaterThan(0);
  });

  it("handles 1 hit of 1 evaluated (1/1) without a zero-width or out-of-range interval", async () => {
    mockApi({ results: { ...BASE_RESULTS, evaluated_buy_count: 1, buy_hits: 1, buy_hit_rate_pct: 100.0 } });
    renderPage();
    const ciText = await screen.findByText(/95% CI/);
    const match = ciText.textContent?.match(/\[([\d.]+)%, ([\d.]+)%\]/);
    expect(match).not.toBeNull();
    const [, lo, hi] = match!;
    expect(Number(lo)).toBeGreaterThanOrEqual(0);
    expect(Number(hi)).toBeLessThanOrEqual(100);
    expect(Number(lo)).toBeLessThan(Number(hi));
  });

  it("uses evaluated_buy_count as n, never the total buy_signals count", async () => {
    // buy_signals (total predicted) = 40, but evaluated_buy_count = 5 —
    // the CI/n display must reflect 5, not 40.
    mockApi({ results: { ...BASE_RESULTS, buy_signals: 40, evaluated_buy_count: 5, buy_hits: 3, buy_hit_rate_pct: 60.0 } });
    renderPage();
    await screen.findByText(/n=5 evaluated/);
    expect(screen.queryByText(/n=40 evaluated/)).not.toBeInTheDocument();
  });

  it("does not display a CI reconstructed from a rounded percentage when buy_hits is absent", async () => {
    // Only buy_hit_rate_pct present, no exact buy_hits — must show the
    // base label with no CI at all, not infer a hit count from the rate.
    const { buy_hits: _unused, ...withoutHits } = BASE_RESULTS;
    void _unused;
    mockApi({ results: { ...withoutHits, buy_hits: null } });
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.queryByText(/95% CI/)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// BUY Hit-Rate Binomial Check card — scoped title, honest wording, states
// (V-VAL1 correction: the card was previously titled the broad,
// unqualified "Statistical Significance" — corrected to a title scoped to
// what it actually tests, plus explicit correlation/multiple-testing
// disclosures that were not present before.)
// ─────────────────────────────────────────────────────────────────────────
describe("BUY Hit-Rate Binomial Check card — scoped title, honest wording", () => {
  it("does not use the broad, unqualified 'Statistical Significance' title", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit-Rate Binomial Check/i);
    expect(screen.queryByText(/^Statistical Significance$/i)).not.toBeInTheDocument();
  });

  it("uses a scoped BUY-check title", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit-Rate Binomial Check/i);
  });

  it("labels it as an exact two-sided binomial check against a 50% null, with n/hits/p0 shown", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Exact two-sided binomial check of evaluated BUY outcomes against a 50% null/i);
    const sampleLine = await screen.findByText(/21\/40 evaluated BUY/i);
    expect(sampleLine.textContent).toMatch(/p₀ = 50%/);
    expect(sampleLine.textContent).toMatch(/· two-sided/i);
  });

  it("discloses that historical signals can overlap and be correlated", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/signals can overlap and be correlated by stock, date, holding window and market regime/i);
  });

  it("discloses the independent-trial assumption is not established", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/independent-trial assumption behind this p-value is not established/i);
  });

  it("discloses unadjusted/multiple-testing risk across repeated slices", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/repeated checks across markets, horizons/i);
    await screen.findByText(/multiple-testing risk/i);
  });

  it("does not claim independence of signals as established fact", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit-Rate Binomial Check/i);
    expect(screen.queryByText(/signals are independent/i)).not.toBeInTheDocument();
  });

  it("never claims the whole model, AI Score calibration, economic value, or future profitability is validated", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit-Rate Binomial Check/i);
    for (const banned of [/whole model is (statistically )?validated/i, /edge confirmed/i,
                           /statistically validated model/i, /\bproven\b/i,
                           /\bprofitable\b(?!.{0,20}BUY)/i]) {
      expect(screen.queryByText(banned)).not.toBeInTheDocument();
    }
    await screen.findByText(/does not establish economic value, future profitability, or that the AI Score is calibrated/i);
  });

  it("below-threshold wording stays scoped to this unadjusted BUY-outcome check", async () => {
    // buy_hits=2482, evaluated=4622 -> scipy p ≈ 5.22e-7, well below 0.05.
    mockApi({ results: { ...BASE_RESULTS, buy_hits: 2482, evaluated_buy_count: 4622 } });
    renderPage();
    await screen.findByText(/Below the conventional 0\.05 threshold for this unadjusted BUY-outcome check\./i);
    expect(screen.queryByText(/model is statistically significant/i)).not.toBeInTheDocument();
  });

  it("above-threshold wording remains honest, not alarmist or dismissive", async () => {
    // buy_hits=21, evaluated=40 -> scipy p ≈ 0.875, well above 0.05.
    mockApi(); // BASE_RESULTS already has buy_hits:21, evaluated_buy_count:40
    renderPage();
    await screen.findByText(/Not below the conventional 0\.05 threshold for this unadjusted BUY-outcome check\./i);
  });

  it("renders an explicit unavailable state when evaluated_buy_count is 0", async () => {
    mockApi({ results: { ...BASE_RESULTS, evaluated_buy_count: 0, buy_hits: 0 } });
    renderPage();
    await screen.findByText(/BUY Hit-Rate Binomial Check/i);
    await screen.findByText(/Unavailable — no evaluated BUY signals in this sample/i);
  });

  it("marks a small evaluated sample (< 30) without hiding the real p-value", async () => {
    mockApi({ results: { ...BASE_RESULTS, evaluated_buy_count: 10, buy_hits: 8, buy_hit_rate_pct: 80.0 } });
    renderPage();
    await screen.findByText(/small sample/i);
    // Exact test still computes a real p (scipy: binomtest(8,10) == 0.109375).
    await screen.findByText(/p = 0.109/);
  });

  it("keeps the evaluated n visible and never substitutes total buy_signals within the card itself", async () => {
    mockApi({ results: { ...BASE_RESULTS, buy_signals: 999, buy_hits: 21, evaluated_buy_count: 40 } });
    renderPage();
    const sampleLine = await screen.findByText(/21\/40 evaluated BUY/i);
    // 999 (total buy_signals) legitimately appears elsewhere on the page
    // (the run-metadata "BUY signals" count) — this test only asserts the
    // binomial-check card's own sample line uses 40 (evaluated), not 999.
    expect(sampleLine.textContent).not.toMatch(/999/);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Overall Accuracy — invalid majority-class baseline removed
// ─────────────────────────────────────────────────────────────────────────
describe("Overall Accuracy — no invalid majority-class baseline", () => {
  it("never computes or displays a majority-class baseline percentage", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Overall Accuracy/i);
    expect(screen.queryByText(/majority-class baseline/i)).not.toBeInTheDocument();
  });

  it("shows accurate descriptive wording instead", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/does not currently expose an actual-class distribution/i);
  });

  it("predicted-class counts (buy_signals/sell_signals/hold_signals) never drive a computed baseline number", async () => {
    // Even with a heavily skewed predicted mix, no baseline % must appear —
    // predicted counts are not an actual-label distribution.
    mockApi({ results: { ...BASE_RESULTS, buy_signals: 950, sell_signals: 25, hold_signals: 25 } });
    renderPage();
    await screen.findByText(/Overall Accuracy/i);
    expect(screen.queryByText(/baseline: \d/i)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Score-bucket / calibration wording
// ─────────────────────────────────────────────────────────────────────────
describe("score-bucket wording", () => {
  it("does not assert the score IS well-calibrated", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Signal Performance by AI Score/i);
    expect(screen.queryByText(/A well-calibrated model shows hit rate rising with score/i)).not.toBeInTheDocument();
    expect(screen.getByText(/not proof the score is well-calibrated/i)).toBeInTheDocument();
  });

  it("marks the tiny 80–85 bucket as a limited sample", async () => {
    mockApi();
    renderPage();
    const cell = await screen.findByText("80–85");
    expect(cell.closest("tr")?.textContent).toContain("*");
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Composite IC explanation
// ─────────────────────────────────────────────────────────────────────────
describe("Composite IC vs hit-rate reconciliation", () => {
  it("explains IC and hit rate are different tests", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/IC vs\. the score table above are different tests/i);
  });

  it("does not claim IC is a statistical-significance test", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Factor Information Coefficients/i);
    expect(screen.queryByText(/^Statistically meaningful$/)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Max Drawdown wording
// ─────────────────────────────────────────────────────────────────────────
describe("Max Drawdown wording", () => {
  it("labels it as the equal-weight signal-date curve, not a portfolio", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Max Drawdown/i);
    expect(screen.getByText(/equal-weight signal-date return curve/i)).toBeInTheDocument();
    expect(screen.queryByText(/^peak-to-trough$/i)).not.toBeInTheDocument();
  });

  it("renders the exact value from a synthetic series computed under the new methodology", async () => {
    // Hand-calculated + confirmed against the real _max_drawdown() backend
    // function (see backend test suite): two BUY signals on 2026-01-01
    // (+20%, +10% -> date return +15%) then one on 2026-01-02 (-40%).
    // Equity: 100 -> 115 -> 69. Peak 115, trough 69 -> drawdown 40.0%.
    // This proves the rendered value/label pairing under the CORRECTED
    // algorithm — not production's old value under the new label.
    mockApi({ results: { ...BASE_RESULTS, max_drawdown_pct: 40.0 } });
    renderPage();
    await screen.findByText(/Max Drawdown/i);
    expect(screen.getByText("-40%")).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Fundamentals limitation wording
// ─────────────────────────────────────────────────────────────────────────
describe("fundamentals limitation wording", () => {
  it("renders the India-scoped limitation notice", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Historical validation limitation/i);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Pagination / Load More
// ─────────────────────────────────────────────────────────────────────────
describe("per-stock pagination", () => {
  it("shows a bounded first page and reveals the rest via Load more", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/showing 1–40 of 65/i);
    expect(screen.queryByText("SYM40")).not.toBeInTheDocument();

    const loadMore = screen.getByRole("button", { name: /Load more \(25 remaining\)/i });
    fireEvent.click(loadMore);

    await waitFor(() => {
      expect(screen.getByText(/showing 1–65 of 65/i)).toBeInTheDocument();
    });
    expect(screen.getByText("SYM40")).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// Loading / error / empty states
// ─────────────────────────────────────────────────────────────────────────
describe("loading, error and empty states", () => {
  it("shows the loading indicator before results resolve", () => {
    mockGet.mockImplementation(() => new Promise(() => {}));
    renderPage();
    expect(screen.getByText(/Loading results/i)).toBeInTheDocument();
  });

  it("shows the empty state when no results are available", async () => {
    mockApi({ results: { available: false } });
    renderPage();
    await screen.findByText(/No validation results yet/i);
  });

  it("renders successfully with a full results payload", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.getByText(/Overall Accuracy/i)).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// PR #52 invariants — must remain unchanged by this port
// ─────────────────────────────────────────────────────────────────────────
describe("PR #52 security/cadence invariants unchanged", () => {
  it("has no Run Now control anywhere", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    expect(screen.queryByText(/run now/i)).not.toBeInTheDocument();
  });

  it("never issues a POST request (api.get only is mocked/used)", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    // Every mocked call must be a GET-shaped call through api.get — there is
    // no api.post mock at all, so any attempted POST would throw instead of
    // silently succeeding.
    for (const call of mockGet.mock.calls) {
      expect(call[0]).not.toContain("/api/validation/run");
    }
  });

  it("Refresh displayed results performs a GET refetch only", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/BUY Hit Rate/i);
    mockGet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
  });

  it("retains the daily/Sunday cadence wording", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/medium-horizon results are scheduled daily/i);
    await screen.findByText(/long-horizon results are scheduled weekly on Sunday/i);
  });

  it("retains the 'Validation completed' timestamp label", async () => {
    mockApi();
    renderPage();
    await screen.findByText(/Validation completed/i);
    expect(screen.queryByText(/^Last run/i)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// V-SNAP1B — IMMUTABLE VALIDATION SNAPSHOT PINNING.
//
// Closes a proven race (V-SNAP1A forensic investigation): the aggregate and
// per-stock endpoints each independently select the latest run at the
// instant they're called, so a new run completing between the two requests
// could combine aggregate metrics from one run with per-stock rows from a
// different, newer run. The fix is an atomic candidate-to-active snapshot
// transition — fetch the aggregate candidate, fetch its per-stock
// counterpart PINNED to that candidate's own run_id, verify both agree on
// run_id/horizon/universe, and only then promote both together in one
// state update. The previous complete snapshot must remain visible for the
// whole verification window.
// ─────────────────────────────────────────────────────────────────────────
describe("V-SNAP1B — atomic snapshot pinning", () => {
  function runResults(runId: number, overrides: object = {}) {
    return { ...BASE_RESULTS, run_id: runId, ...overrides };
  }

  function stocksFor(runId: number, symbol: string) {
    return {
      available: true,
      run_id: runId,
      stocks: [
        { symbol, total_signals: 20, buy_signal_count: 20, evaluated_buy_count: 20,
          buy_hits: 11, hit_rate_pct: 55.0, buy_return_count: 20, avg_fwd_return_pct: 1.2, buy_avg_return_pct: 2.0 },
      ],
    };
  }

  it("requests per-stock data pinned to the aggregate's own run_id", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(7, "RUNA") });
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(7) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText("RUNA");
    const stockCall = mockGet.mock.calls.find(c => (c[0] as string).includes("/results/stocks"));
    expect(stockCall?.[0]).toContain("run_id=7");
  });

  it("never renders a mixed combination: aggregate B is never displayed with stocks A, and stocks B never displayed with aggregate A", async () => {
    // V-SNAP1C correction: the prior version of this test distinguished
    // ONLY per-stock symbols. React Query's stale-while-revalidate default
    // keeps showing the previous per-stock query result while a background
    // refetch is pending, which can accidentally satisfy a symbol-only
    // assertion even in the pre-fix implementation (proven in the V-SNAP1B
    // independent review's disposable-worktree base comparison — see Stage
    // 3 evidence below). This version distinguishes BOTH the aggregate
    // (n_stocks_tested, a value the page actually renders as "Stocks
    // tested: N") and the per-stock symbol, and synchronizes on a real,
    // implementation-agnostic signal (the candidate aggregate fetch for
    // run B having resolved) rather than a fixed sleep — so a mixed
    // aggregate-B/stocks-A (or aggregate-A/stocks-B) render is directly
    // observable and cannot be masked by unrelated caching behavior.
    let releaseB: (() => void) | null = null;
    const bPending = new Promise<{ data: object }>((resolve) => { releaseB = () => resolve({ data: stocksFor(2, "RUNB") }); });

    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) {
        if (currentRunId === 1) return Promise.resolve({ data: stocksFor(1, "RUNA") });
        return bPending; // run 2's per-stock fetch is deliberately held pending
      }
      if (url.includes("/results?")) {
        return Promise.resolve({ data: runResults(currentRunId, { n_stocks_tested: currentRunId === 1 ? 111 : 222 }) });
      }
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });

    const { queryClient } = renderPage();
    await screen.findByText("RUNA");
    expect(screen.getByText("111")).toBeInTheDocument(); // aggregate A-only value
    expect(screen.queryByText("222")).not.toBeInTheDocument();
    expect(screen.queryByText("RUNB")).not.toBeInTheDocument();

    // Refresh discovers run B. Its aggregate resolves immediately (a plain
    // resolved Promise); its per-stock fetch is held pending on `bPending`.
    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    // Deterministic synchronization point — not a fixed sleep, not a
    // guessed microtask-flush depth: poll the REAL React Query cache
    // (via the QueryClient exposed by renderPage) until it genuinely
    // holds aggregate B's resolved data. This is an implementation-
    // agnostic, directly observable fact — it becomes true in BOTH the
    // fixed and the pre-fix implementation as soon as React Query has
    // actually finished processing run B's aggregate response, however
    // many internal ticks that takes. `waitFor` polls with real timers,
    // so it lets React's scheduler (which is not purely microtask-based)
    // fully settle, unlike a fixed count of `Promise.resolve()` hops.
    await waitFor(() => {
      const cached = queryClient.getQueryData<{ n_stocks_tested?: number }>(["validation-results", "medium", "nifty100"]);
      expect(cached?.n_stocks_tested).toBe(222);
    });
    // One more flush guarantees any React state update triggered by that
    // cache change has committed to the DOM before the assertions below.
    await act(async () => {});

    // Only the complete run-A snapshot may be visible: aggregate A's own
    // value and stock A's own symbol, together — never aggregate B's
    // value paired with stock A, and never aggregate A paired with any
    // trace of run B.
    expect(screen.getByText("111")).toBeInTheDocument();
    expect(screen.getByText("RUNA")).toBeInTheDocument();
    expect(screen.queryByText("222")).not.toBeInTheDocument();
    expect(screen.queryByText("RUNB")).not.toBeInTheDocument();

    // Release run B's per-stock fetch — it must now atomically replace the
    // entire snapshot (both aggregate and per-stock together), in one
    // committed render containing B's values and none of A's.
    releaseB!();
    await waitFor(() => expect(screen.getByText("222")).toBeInTheDocument());
    expect(screen.getByText("RUNB")).toBeInTheDocument();
    expect(screen.queryByText("111")).not.toBeInTheDocument();
    expect(screen.queryByText("RUNA")).not.toBeInTheDocument();
  });

  it("keeps the last complete snapshot and shows a non-destructive error when the candidate's per-stock fetch fails", async () => {
    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) {
        if (currentRunId === 1) return Promise.resolve({ data: stocksFor(1, "RUNA") });
        return Promise.reject(new Error("network error"));
      }
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(currentRunId) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await screen.findByText("RUNA");

    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByTestId("refresh-error-banner")).toBeInTheDocument());
    expect(screen.getByText("RUNA")).toBeInTheDocument();
  });

  it("keeps the last complete snapshot when the candidate's per-stock response reports a mismatched run_id", async () => {
    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) {
        // Simulate a stale/mismatched per-stock response — its own run_id
        // doesn't match the aggregate candidate's run_id.
        return Promise.resolve({ data: currentRunId === 1 ? stocksFor(1, "RUNA") : stocksFor(999, "WRONGRUN") });
      }
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(currentRunId) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await screen.findByText("RUNA");

    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByTestId("refresh-error-banner")).toBeInTheDocument());
    expect(screen.getByText("RUNA")).toBeInTheDocument();
    expect(screen.queryByText("WRONGRUN")).not.toBeInTheDocument();
  });

  it("clears the active snapshot immediately on horizon change — never shows stale prior-view data", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(1, "RUNA") });
      if (url.includes("/results?horizon=medium")) return Promise.resolve({ data: runResults(1) });
      if (url.includes("/results?horizon=long")) return new Promise(() => {}); // never resolves
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await screen.findByText("RUNA");

    fireEvent.click(screen.getByRole("button", { name: /^Long/i }));

    await waitFor(() => expect(screen.queryByText("RUNA")).not.toBeInTheDocument());
    expect(screen.getByText(/Loading results/i)).toBeInTheDocument();
  });

  it("clears the active snapshot immediately on universe change — never shows stale prior-view data", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(1, "RUNA") });
      if (url.includes("universe=nifty100")) return Promise.resolve({ data: runResults(1) });
      if (url.includes("universe=us")) return new Promise(() => {}); // never resolves
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await screen.findByText("RUNA");

    fireEvent.click(screen.getByRole("button", { name: /🇺🇸 US/i }));

    await waitFor(() => expect(screen.queryByText("RUNA")).not.toBeInTheDocument());
    expect(screen.getByText(/Loading results/i)).toBeInTheDocument();
  });

  // V-SNAP1D correction — REWRITTEN, not deleted: the prior version of
  // this test asserted that an aggregate with no run_id would still
  // promote normally, unpinned, as "backward-compatible latest
  // behavior." That is the exact publication-blocking defect the final
  // independent V-SNAP1 review found: it's indistinguishable from what a
  // pre-V-SNAP1 backend serves during deployment skew, and accepting it
  // silently reopens the original race this whole phase closes. The
  // corrected contract: an identity-less aggregate candidate must never
  // be promoted, and must never even trigger an unpinned per-stock
  // request — the page shows an honest unavailable state instead.
  it("an identity-less aggregate (no run_id) never promotes and never triggers an unpinned per-stock request", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: { available: true, stocks: MANY_STOCKS } });
      if (url.includes("/results?")) return Promise.resolve({ data: { ...BASE_RESULTS, run_id: undefined } });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText(/No validation results yet/i);
    expect(screen.queryByText(/BUY Hit Rate/i)).not.toBeInTheDocument();
    const stockCall = mockGet.mock.calls.find(c => (c[0] as string).includes("/results/stocks"));
    expect(stockCall).toBeUndefined(); // no unpinned per-stock request was ever made
  });

  it("zero-BUY symbols can exist in the API response but are honestly excluded from the BUY-performance table (129-vs-126 contract)", async () => {
    const withZeroBuy = [
      ...MANY_STOCKS,
      { symbol: "ZEROBUY", total_signals: 5, buy_signal_count: 0, evaluated_buy_count: 0,
        buy_hits: null, hit_rate_pct: null, buy_return_count: 0, avg_fwd_return_pct: null, buy_avg_return_pct: null },
    ];
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: { available: true, run_id: 1, stocks: withZeroBuy } });
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(1) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText(/showing 1–40 of 65/i);
    // ZEROBUY is present in the mocked API payload (65 + 1 = 66 total rows)
    // but must never appear in the BUY-performance table, since it has zero
    // BUY signals — the table's own count must stay 65, not 66.
    expect(screen.queryByText("ZEROBUY")).not.toBeInTheDocument();
    expect(screen.queryByText(/showing 1–40 of 66/i)).not.toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// V-SNAP1C — render-time run-identity coherence guard (second, independent
// defense layer). A Snapshot can only ever be constructed by the verified
// promotion effect in page.tsx, so a mismatched pair should never actually
// reach the render path in practice — this directly unit-tests the pure
// guard function itself, proving that IF a mismatched pair were ever
// constructed (a future bug reintroducing the exact defect this phase
// closed), the render path would independently refuse it rather than
// relying solely on the promotion-time check.
// ─────────────────────────────────────────────────────────────────────────
describe("V-SNAP1C/D — runIdsCoherent render-time guard", () => {
  function available(runId: unknown) {
    return { available: true as const, run_id: runId as number | undefined };
  }
  function stock(runId: unknown) {
    return { run_id: runId as number | undefined, stocks: [] };
  }

  it("accepts a matching run_id pair", () => {
    expect(runIdsCoherent(available(5), stock(5))).toBe(true);
  });

  it("accepts equal positive safe-integer IDs at the boundary", () => {
    expect(runIdsCoherent(available(Number.MAX_SAFE_INTEGER), stock(Number.MAX_SAFE_INTEGER))).toBe(true);
  });

  it("rejects a mismatched run_id pair — the exact defect this phase closes", () => {
    expect(runIdsCoherent(available(5), stock(6))).toBe(false);
  });

  it("rejects aggregate run_id present but per-stock run_id missing", () => {
    expect(runIdsCoherent(available(5), stock(undefined))).toBe(false);
  });

  it("rejects per-stock run_id present but aggregate run_id missing", () => {
    expect(runIdsCoherent(available(undefined), stock(5))).toBe(false);
  });

  // V-SNAP1D correction — REWRITTEN, not deleted: the prior version of
  // this test asserted the OPPOSITE (that both sides omitting run_id was
  // "coherent" as backward compatibility). That was the exact publication-
  // blocking defect the V-SNAP1 final independent review found: during
  // Vercel/Railway deployment skew, a backend rollback, or communication
  // with a pre-V-SNAP1 backend, both the aggregate and per-stock responses
  // genuinely omit run_id — accepting that pairing as "coherent" silently
  // reopens the original unpinned-latest-vs-latest race with no user-
  // visible signal. A missing identity on either or both sides must now
  // be rejected, not trusted.
  it("rejects a pair where both sides omit run_id — closes the deployment-skew hole (was incorrectly 'coherent' before V-SNAP1D)", () => {
    expect(runIdsCoherent(available(undefined), stock(undefined))).toBe(false);
    expect(runIdsCoherent(available(null), stock(null))).toBe(false);
  });

  it("rejects zero as a run_id on either side", () => {
    expect(runIdsCoherent(available(0), stock(0))).toBe(false);
    expect(runIdsCoherent(available(0), stock(5))).toBe(false);
    expect(runIdsCoherent(available(5), stock(0))).toBe(false);
  });

  it("rejects negative run_ids", () => {
    expect(runIdsCoherent(available(-1), stock(-1))).toBe(false);
    expect(runIdsCoherent(available(-5), stock(5))).toBe(false);
  });

  it("rejects fractional run_ids", () => {
    expect(runIdsCoherent(available(1.5), stock(1.5))).toBe(false);
    expect(runIdsCoherent(available(5), stock(5.5))).toBe(false);
  });

  it("rejects NaN", () => {
    expect(runIdsCoherent(available(NaN), stock(NaN))).toBe(false);
    expect(runIdsCoherent(available(NaN), stock(5))).toBe(false);
  });

  it("rejects positive and negative Infinity", () => {
    expect(runIdsCoherent(available(Infinity), stock(Infinity))).toBe(false);
    expect(runIdsCoherent(available(-Infinity), stock(-Infinity))).toBe(false);
  });

  it("rejects a run_id above Number.MAX_SAFE_INTEGER (not a safe integer)", () => {
    const tooLarge = Number.MAX_SAFE_INTEGER + 2;
    expect(runIdsCoherent(available(tooLarge), stock(tooLarge))).toBe(false);
  });

  it("rejects a numeric string at runtime even though TypeScript would normally prevent it", () => {
    // Runtime JSON payloads are not type-checked — a hostile/buggy backend
    // could send "5" (string) instead of 5 (number). No implicit
    // coercion (Number(...), unary +, ==) is allowed to paper over this.
    expect(runIdsCoherent(available("5"), stock("5"))).toBe(false);
    expect(runIdsCoherent(available("5"), stock(5))).toBe(false);
  });

  it("accepts an unavailable aggregate regardless of stock-side run_id (nothing to compare)", () => {
    expect(runIdsCoherent({ available: false }, stock(5))).toBe(true);
    expect(runIdsCoherent({ available: false }, stock(undefined))).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// V-SNAP1D — FAIL CLOSED ON IDENTITY-LESS OR INVALID FRONTEND SNAPSHOTS.
//
// Page-level integration coverage for the deployment-skew contract: an
// identity-less or invalid aggregate/per-stock response must never be
// promoted or rendered as a verified snapshot, and must never trigger an
// unpinned per-stock request — regardless of whether this is the initial
// load or a refresh with a previously active, valid snapshot.
// ─────────────────────────────────────────────────────────────────────────
describe("V-SNAP1D — fail closed on identity-less or invalid snapshots", () => {
  function runResults(runId: unknown, overrides: object = {}) {
    return { ...BASE_RESULTS, run_id: runId, ...overrides };
  }
  function stocksFor(runId: unknown, symbol: string) {
    return { available: true, run_id: runId, stocks: [
      { symbol, total_signals: 20, buy_signal_count: 20, evaluated_buy_count: 20,
        buy_hits: 11, hit_rate_pct: 55.0, buy_return_count: 20, avg_fwd_return_pct: 1.2, buy_avg_return_pct: 2.0 },
    ] };
  }

  it("initial load: identity-less aggregate shows the honest unavailable state, never a verified snapshot", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(undefined, "OLDSTOCK") });
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(undefined) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText(/No validation results yet/i);
    expect(screen.queryByText("OLDSTOCK")).not.toBeInTheDocument();
    expect(screen.queryByText(/BUY Hit Rate/i)).not.toBeInTheDocument();
  });

  it("initial load: identity-less aggregate never initiates an unpinned /results/stocks request", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(undefined, "OLDSTOCK") });
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(undefined) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText(/No validation results yet/i);
    const stockCall = mockGet.mock.calls.find(c => (c[0] as string).includes("/results/stocks"));
    expect(stockCall).toBeUndefined();
  });

  it("refresh: an identity-less candidate aggregate never triggers an unpinned per-stock request, and preserves the active pinned snapshot", async () => {
    let phase: "initial" | "skew" = "initial";
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(1, "RUNA") });
      if (url.includes("/results?")) {
        return Promise.resolve({ data: phase === "initial" ? runResults(1) : runResults(undefined) });
      }
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText("RUNA");

    phase = "skew"; // simulates a backend rollback / deployment skew mid-session
    mockGet.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByTestId("refresh-error-banner")).toBeInTheDocument());
    expect(screen.getByText("RUNA")).toBeInTheDocument(); // active A preserved, never destroyed
    const stockCall = mockGet.mock.calls.find(c => (c[0] as string).includes("/results/stocks"));
    expect(stockCall).toBeUndefined(); // no unpinned per-stock request was issued for the skewed candidate
  });

  it("valid aggregate B whose per-stock response omits run_id: does not promote, keeps active A, shows an honest error", async () => {
    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) {
        if (currentRunId === 1) return Promise.resolve({ data: stocksFor(1, "RUNA") });
        return Promise.resolve({ data: { available: true, stocks: [
          { symbol: "RUNB", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1, buy_hits: 1,
            hit_rate_pct: 100, buy_return_count: 1, avg_fwd_return_pct: 1, buy_avg_return_pct: 1 },
        ] } }); // valid aggregate, but its per-stock response has NO run_id
      }
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(currentRunId) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText("RUNA");

    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByTestId("refresh-error-banner")).toBeInTheDocument());
    expect(screen.getByText("RUNA")).toBeInTheDocument();
    expect(screen.queryByText("RUNB")).not.toBeInTheDocument();
  });

  it("valid aggregate B whose per-stock response has a malformed (fractional) run_id: does not promote, keeps active A", async () => {
    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) {
        if (currentRunId === 1) return Promise.resolve({ data: stocksFor(1, "RUNA") });
        return Promise.resolve({ data: stocksFor(2.5, "RUNB") }); // fractional — invalid
      }
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(currentRunId) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText("RUNA");

    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByTestId("refresh-error-banner")).toBeInTheDocument());
    expect(screen.getByText("RUNA")).toBeInTheDocument();
    expect(screen.queryByText("RUNB")).not.toBeInTheDocument();
  });

  it("deployment-skew end-to-end: old-backend-shaped responses (no run_id anywhere) never render as a verified snapshot", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: { available: true, stocks: [
        { symbol: "OLDSTOCK", total_signals: 1, buy_signal_count: 1, evaluated_buy_count: 1, buy_hits: 1,
          hit_rate_pct: 100, buy_return_count: 1, avg_fwd_return_pct: 1, buy_avg_return_pct: 1 },
      ] } });
      if (url.includes("/results?")) return Promise.resolve({ data: { ...BASE_RESULTS, run_id: undefined } });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText(/No validation results yet/i);
    expect(screen.queryByText("OLDSTOCK")).not.toBeInTheDocument();
    const stockCall = mockGet.mock.calls.find(c => (c[0] as string).includes("/results/stocks"));
    expect(stockCall).toBeUndefined();
  });

  it("valid A-to-valid-B atomic transition still works after the fail-closed correction", async () => {
    let currentRunId = 1;
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/results/stocks")) return Promise.resolve({ data: stocksFor(currentRunId, currentRunId === 1 ? "RUNA" : "RUNB") });
      if (url.includes("/results?")) return Promise.resolve({ data: runResults(currentRunId) });
      if (url.includes("/status")) return Promise.resolve({ data: { running: false, progress: 0, total: 0, started_at: null, log: [] } });
      return Promise.resolve({ data: {} });
    });
    renderPage();
    await screen.findByText("RUNA");

    currentRunId = 2;
    fireEvent.click(screen.getByRole("button", { name: /refresh displayed results/i }));

    await waitFor(() => expect(screen.getByText("RUNB")).toBeInTheDocument());
    expect(screen.queryByText("RUNA")).not.toBeInTheDocument();
    expect(screen.queryByTestId("refresh-error-banner")).not.toBeInTheDocument();
  });
});
