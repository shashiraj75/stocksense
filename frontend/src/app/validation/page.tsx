"use client";
import { useState, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/utils/api";
import { resolveActiveJobView } from "@/utils/validationJobView";
import { DataLimitationsNotice } from "@/components/DataLimitationsNotice";
import {
  FlaskConical, TrendingUp, TrendingDown, Target, Zap,
  BarChart3, CheckCircle2, XCircle, AlertCircle, Loader2, RefreshCw,
} from "lucide-react";

type ScoreBucket = {
  score_range: string;
  count: number;
  hit_rate_pct: number | null;
  avg_return_pct: number | null;
};

type FactorIC = {
  tech: number | null;
  rs: number | null;
  obv: number | null;
  mfi: number | null;
  composite: number | null;
};

type ValidationResult = {
  available: boolean;
  message?: string;
  horizon?: string;
  // V-SNAP1B — the canonical val_runs.id for this aggregate result,
  // authoritative over anything the stored summary JSON might contain.
  // Used to pin the per-stock fetch to this exact run, closing the
  // aggregate/per-stock snapshot race.
  run_id?: number | null;
  n_stocks_tested?: number;
  run_at?: string;
  total_signals?: number;
  buy_signals?: number;
  sell_signals?: number;
  // V-VAL1 evaluated-cohort fields (additive — buy_signals above is the
  // legacy predicted-BUY count, kept unchanged). evaluated_buy_count/
  // buy_hits are the exact integers the Wilson interval must use — never
  // buy_signals, never a hit count reconstructed from buy_hit_rate_pct.
  evaluated_buy_count?: number;
  buy_hits?: number | null;
  buy_return_count?: number;
  overall_accuracy_pct?: number | null;
  buy_hit_rate_pct?: number | null;
  sell_hit_rate_pct?: number | null;
  avg_return_on_buy_pct?: number | null;
  avg_alpha_on_buy_pct?: number | null;
  avg_return_on_sell_pct?: number | null;
  avg_return_benchmark_pct?: number | null;
  buy_outperformance_pct?: number | null;
  sharpe_on_buys?: number | null;
  sharpe_on_alphas?: number | null;
  profitable_buy_pct?: number | null;
  beat_benchmark_pct?: number | null;
  max_consecutive_wrong?: number | null;
  max_consecutive_right?: number | null;
  max_drawdown_pct?: number | null;
  score_buckets?: ScoreBucket[];
  factor_ic?: FactorIC;
  nifty_avg_fwd_return_pct?: number | null;
  hold_signals?: number;
  data_limitations?: {
    fundamentals_point_in_time?: boolean;
    fundamentals_point_in_time_coverage_pct?: number | null;
  } | null;
  // V-FRESH1B — additive, present only on runs computed after this phase
  // shipped; absent on every legacy row. Factual diagnostic evidence
  // only — symbols_with_signals is deliberately never described as
  // "coverage" or "eligibility" anywhere this is rendered (see
  // V-FRESH1A2's corrected forensic finding).
  validation_evidence?: {
    symbols_requested: number;
    symbols_fetch_attempted: number;
    symbols_with_input_data: number;
    symbols_with_sufficient_data: number;
    symbols_processed: number;
    symbols_with_signals: number;
    symbols_with_evaluated_outcomes: number;
    fetch_exception_count: number;
    empty_data_count: number;
    insufficient_data_count: number;
    calculation_exception_count: number;
    input_latest_bar_date_min: string | null;
    input_latest_bar_date_max: string | null;
    signal_date_min: string | null;
    signal_date_max: string | null;
    evaluated_exit_date_min: string | null;
    evaluated_exit_date_max: string | null;
  } | null;
  // V-FRESH1B — always present, always "unknown" in this phase (Option
  // A: a durable metadata-and-disclosure foundation, not a complete
  // freshness classifier — see backend's _compute_freshness docstring).
  freshness?: {
    status: "unknown";
    reason:
      | "schedule_not_defined"
      | "legacy_run_without_evidence_metadata"
      | "calendar_or_completion_slo_unavailable";
    validation_completed_at: string | null;
    input_data_recency: "unknown";
    outcome_evidence_recency: "unknown";
  } | null;
};

// Cohort contract (V-VAL1, matches services/validation_engine.py's
// get_per_stock_results): buy_signal_count is every persisted BUY row;
// evaluated_buy_count is the subset with a resolved correctness outcome
// (today always equal to buy_signal_count by application invariant, but
// not assumed so — see the backend docstring); hit_rate_pct is null, not
// a fabricated 0%, when evaluated_buy_count is 0. buy_return_count is the
// separate cohort behind buy_avg_return_pct (BUY rows with a valid
// return), independent of the hit-rate cohort.
type StockResult = {
  symbol: string;
  total_signals: number;
  buy_signal_count: number;
  evaluated_buy_count: number;
  buy_hits: number | null;
  hit_rate_pct: number | null;
  buy_return_count: number;
  avg_fwd_return_pct: number | null;
  buy_avg_return_pct: number | null;
};

type ValidationJob = {
  job_id: string;
  market: string;
  universe_id: string;
  universe_version?: string;
  benchmark?: string;
  horizon: string;
  status: string;
  processed?: number;
  total?: number;
  current_symbol?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

type RunStatus = {
  running: boolean;
  progress: number;
  total: number;
  started_at: string | null;
  log: string[];
  // Immutable identity of the active (or last) run — the backend binds every
  // job to exactly one market/universe/horizon so a US run can never be
  // rendered as a Nifty 100 run (validation job-identity fix, 2026-07).
  job?: ValidationJob | null;
};

type StockDataPayload = { available?: boolean; run_id?: number | null; stocks: StockResult[] };

// V-SNAP1B — an atomically-promoted, self-consistent pair: `results` and
// `stockData` in a Snapshot always describe the SAME validation run for the
// SAME horizon/universe. Never construct one outside promoteSnapshot below.
type Snapshot = {
  horizon: string;
  universe: string;
  results: ValidationResult;
  stockData: StockDataPayload;
};

// V-SNAP1D — canonical frontend validity predicate for a validation run
// identity. The backend uses a positive auto-incrementing/BIGSERIAL
// identity (val_runs.id) — a valid canonical run_id must be a genuine
// JS `number`, finite, an integer, a SAFE integer, and strictly greater
// than zero. No coercion (`Number(...)`, `parseInt`, unary `+`, `==`, or
// truthiness) — a runtime payload that sends a numeric STRING, 0, a
// negative value, a fraction, NaN, +/-Infinity, or a value beyond
// Number.MAX_SAFE_INTEGER is rejected outright, not silently accepted.
function isValidRunId(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

// V-SNAP1C/D — second, independent defense layer (redundant with, not a
// replacement for, the promotion-time `identityMatches` check and the
// `seqRef` sequence guard). A Snapshot is only ever constructed by the
// verified promotion effect below, so this should always be true by
// construction — but the render path re-checks it anyway, so a future
// edit that adds another `setActive()` call site (or any other bug that
// slips a mismatched pair through) cannot silently render mixed
// aggregate/per-stock data even if that first layer is ever broken.
//
// V-SNAP1D correction: the prior version of this function treated a pair
// where BOTH sides omitted run_id as "coherent," reasoning it as legacy-
// backend backward compatibility. The final independent V-SNAP1 review
// found this reopens the exact race V-SNAP1 exists to close during any
// Vercel/Railway deployment skew, a backend rollback, or communication
// with a pre-V-SNAP1 backend — both endpoints would independently select
// their own "latest" run again, exactly as before this whole phase, with
// no signal to the user that the displayed pairing was never actually
// verified. A missing OR invalid identity on either side is now rejected,
// never trusted — there is no backward-compatibility carve-out.
export function runIdsCoherent(results: ValidationResult, stockData: StockDataPayload): boolean {
  if (results.available !== true) return true; // unavailable result has no per-stock run to compare against
  return isValidRunId(results.run_id) && isValidRunId(stockData.run_id) && results.run_id === stockData.run_id;
}

const HORIZONS = [
  { key: "short",  label: "Short",  sub: "5-day forward" },
  { key: "medium", label: "Medium", sub: "21-day forward" },
  { key: "long",   label: "Long",   sub: "63-day forward" },
] as const;

const UNIVERSES = [
  { key: "nifty100", label: "🇮🇳 Nifty 100",  sub: "India large-cap" },
  { key: "midcap",   label: "🇮🇳 Midcap",     sub: "India mid-cap" },
  { key: "us",       label: "🇺🇸 US",         sub: "S&P 500 basket" },
] as const;

function StatCard({
  label, value, sub, color = "text-white", icon,
}: {
  label: string; value: string | null; sub?: string; color?: string; icon?: React.ReactNode;
}) {
  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-4">
      <div className="flex items-center gap-2 mb-2">
        {icon && <span className="text-gray-400">{icon}</span>}
        <p className="text-xs text-gray-500">{label}</p>
      </div>
      <p className={`text-2xl font-bold font-mono ${color}`}>
        {value ?? <span className="text-gray-600 text-lg">—</span>}
      </p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

function ICBar({ label, value, color }: { label: string; value: number | null; color: string }) {
  if (value === null) return null;
  const pct = Math.round(Math.min(100, Math.abs(value) * 500));
  const isPositive = value >= 0;
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-gray-400">{label}</span>
        <span className={`font-mono font-semibold ${isPositive ? "text-green-400" : "text-red-400"}`}>
          IC = {value > 0 ? "+" : ""}{value.toFixed(4)}
        </span>
      </div>
      <div className="h-1.5 bg-dark-border rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${isPositive ? color : "bg-red-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-gray-600">
        {Math.abs(value) > 0.05
          ? "✅ Meaningful by convention (|IC| > 0.05) — not a significance test"
          : Math.abs(value) > 0.02
          ? "🟡 Weak signal"
          : "❌ Near zero — noise"}
      </p>
    </div>
  );
}

// Exact two-sided binomial test of evaluated BUY outcomes against a 50%
// null hit rate. Standard definition (matches R's binom.test()/scipy's
// binomtest(alternative="two-sided"), verified against scipy in the
// backend test suite for these exact vectors): sum P(X=i) over every
// outcome i in Binomial(evaluatedCount, 0.5) whose probability is <= the
// probability of the OBSERVED outcome (successes).
//
// V-VAL1 correction: the prior implementation was a normal (Gaussian)
// APPROXIMATION to the binomial CDF — mislabeled by omission, since the
// page called the result "Statistical Significance" with no qualifier —
// and reconstructed its "successes" input from the ROUNDED displayed
// hit-rate percentage times the TOTAL predicted BUY count
// (Math.round(hitRatePct/100 * buy_signals)), not the exact evaluated
// hit count. Both defects are fixed here: this is now a genuine exact
// test (no normal approximation, no minimum-n gate — unlike the old
// n>=30 requirement, an exact test is valid for any n>=1) using the
// exact integer buy_hits/evaluated_buy_count fields.
//
// Two-sided, not one-sided: the prior code's z>=0/z<0 branching computed
// a one-tailed p-value in whichever direction the observed rate landed —
// not a standard, named hypothesis. This correction adopts the standard
// two-sided test explicitly (the more conservative choice — a two-sided
// p-value is never smaller than the corresponding one-sided p-value, so
// this cannot manufacture a more "favourable" significance result).
//
// No new dependency: log-factorial computed once via a cumulative sum,
// giving an O(n) exact evaluation without an external stats library.
//
// Numerical/performance safety (V-VAL1 audit, verified — see this file's
// test suite for the corresponding assertions, not just this comment):
//   - Complexity is O(n), not O(n²): logFact[] is built once via a single
//     cumulative pass (n additions), and logPmf(i) inside the summation
//     loop is O(1) — three array lookups and fixed-count arithmetic, never
//     a fresh per-call recomputation of the factorial sum.
//   - No overflow: all factorial/probability arithmetic happens in LOG
//     space until the final per-term Math.exp() — n! for realistic n
//     (thousands of signals) would overflow a plain double, but ln(n!)
//     never does.
//   - No unsafe underflow: Math.exp() of a very negative log-probability
//     correctly saturates to exactly 0 (a real double value, not NaN/
//     -Infinity propagating outward) and simply contributes nothing to
//     the sum — the correct behavior for a genuinely negligible outcome.
//   - Tolerance: `tol` scales with |logPk| (the observed outcome's own
//     log-probability magnitude) rather than being a fixed epsilon, so it
//     stays proportionate whether the observed outcome is likely (logPk
//     near 0) or a deep tail event (logPk very negative) — loose enough to
//     absorb the ~n*epsilon floating-point error accumulated by the
//     cumulative logFact sum, tight enough (1e-9 relative) not to
//     conflate genuinely distinct outcome probabilities.
//   - Output is always finite and in [0,1] by construction: p starts at 0
//     and only accumulates non-negative Math.exp() terms (never negative,
//     never NaN), and the final Math.min(1, p) is a defensive ceiling
//     clamp for floating-point summation drift at the boundary (e.g. all
//     outcomes selected, which should sum to ~1.0 but can round to
//     1.0000000000000002).
//   - Runtime: O(n) with n bounded by realistic evaluated-BUY-signal
//     counts (thousands, not millions) executes in low single-digit
//     milliseconds — no risk of blocking the browser's main thread.
export function exactBinomialTwoSidedPValue(
  successes: number, trials: number
): { p: number; significant: boolean } | null {
  if (!Number.isFinite(successes) || !Number.isFinite(trials)) return null;
  if (trials <= 0 || successes < 0 || successes > trials) return null; // malformed input — fail safe
  const n = trials, k = successes, p0 = 0.5;
  const logFact = new Float64Array(n + 1);
  for (let i = 1; i <= n; i++) logFact[i] = logFact[i - 1] + Math.log(i);
  const logP0 = Math.log(p0), log1mP0 = Math.log(1 - p0);
  const logPmf = (i: number) => logFact[n] - logFact[i] - logFact[n - i] + i * logP0 + (n - i) * log1mP0;
  const logPk = logPmf(k);
  const tol = 1e-9 * Math.max(1, Math.abs(logPk));
  let p = 0;
  for (let i = 0; i <= n; i++) {
    if (logPmf(i) <= logPk + tol) p += Math.exp(logPmf(i));
  }
  p = Math.min(1, p);
  return { p, significant: p < 0.05 };
}

// 95% Wilson score interval for a binomial proportion — preferred over the
// naive normal (Wald) interval because it stays inside [0,100] and doesn't
// collapse to a false zero-width interval at n=1 the way Wald does.
//
// Takes EXACT integer successes/evaluatedCount — never total BUY signals,
// never a hit count reconstructed from a rounded displayed percentage
// (e.g. Math.round(53.7/100 * n), which can silently diverge from the
// real integer hit count for some n). Callers must pass the backend's own
// buy_hits/evaluated_buy_count fields.
function wilson95(successes: number, evaluatedCount: number): { lo: number; hi: number } | null {
  if (!evaluatedCount || evaluatedCount <= 0) return null;
  if (!Number.isFinite(successes) || !Number.isFinite(evaluatedCount)) return null;
  if (successes < 0 || successes > evaluatedCount) return null; // malformed input — fail safe, no fabricated output
  const n = evaluatedCount;
  const z = 1.959963985; // 95%
  const phat = successes / n;
  const denom = 1 + (z * z) / n;
  const center = phat + (z * z) / (2 * n);
  const margin = z * Math.sqrt((phat * (1 - phat)) / n + (z * z) / (4 * n * n));
  const lo = (center - margin) / denom;
  const hi = (center + margin) / denom;
  return { lo: Math.round(Math.max(0, lo) * 1000) / 10, hi: Math.round(Math.min(1, hi) * 1000) / 10 };
}

// Centralized "limited sample" display thresholds — a sample below this
// size is dimmed and marked in every table on this page, rather than
// scattering magic numbers through JSX. These are display-only cues, NOT
// claims of statistical significance — sample size alone never proves
// significance; see the "BUY Hit-Rate Binomial Check" card's own p-value
// for the one place on this page that actually runs a significance test
// (and even that one is scoped to a single metric, not the whole model).
const MIN_RELIABLE_SCORE_BUCKET_N = 30;
const MIN_RELIABLE_STOCK_BUY_N = 10;
const PER_STOCK_PAGE_SIZE = 40;

export default function ValidationPage() {
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("medium");
  const [universe, setUniverse] = useState<"nifty100" | "midcap" | "us">("nifty100");
  const [stockPage, setStockPage] = useState(1);
  const qc = useQueryClient();

  // V-SNAP1B — IMMUTABLE VALIDATION SNAPSHOT PINNING.
  //
  // The aggregate `/results` endpoint and the per-stock `/results/stocks`
  // endpoint each independently select the latest eligible run at the
  // instant they're called. Fetching them via two separate hooks that each
  // render directly (the pre-V-SNAP1B design) can transiently combine
  // aggregate metrics from one run with per-stock rows from a different,
  // newer run if a new run completes in the gap between the two requests.
  //
  // Fix: `results` below is only ever the CANDIDATE aggregate fetch — it is
  // never rendered directly. `active` is the only state the JSX below
  // reads, and it is updated in exactly one atomic setState call, only
  // after: (1) the candidate aggregate resolves, (2) its per-stock
  // counterpart is fetched PINNED to that aggregate's own `run_id`, and
  // (3) both are confirmed to describe the same run/horizon/universe. The
  // previous complete snapshot stays visible for the entire duration of
  // that verification — never a partial/mixed intermediate state.
  const { data: candidateResults, isLoading: candidateLoading } = useQuery<ValidationResult>({
    queryKey: ["validation-results", horizon, universe],
    queryFn: () => api.get(`/api/validation/results?horizon=${horizon}&universe=${universe}`).then(r => r.data),
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const [active, setActive] = useState<Snapshot | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // Bumped whenever the view (horizon/universe) changes OR a new candidate
  // aggregate arrives — an in-flight promotion whose sequence number no
  // longer matches the latest is stale and must never commit, closing the
  // window where a slow candidate B's per-stock fetch resolves after an
  // even newer candidate C has already started.
  const seqRef = useRef(0);
  // V-SNAP1D — a ref mirror of `active`, read synchronously inside the
  // promotion effect below to decide "is this the very first snapshot, or
  // is there already a complete one to preserve on failure" WITHOUT
  // depending on `active` in that effect's own dependency array (which
  // would cause it to also re-run — and re-issue a request — every time
  // `setActive` itself fires, an unwanted feedback loop). Always kept in
  // sync with `active` at every setActive/clear call site.
  const activeRef = useRef<Snapshot | null>(null);

  // Horizon/universe change clears the active snapshot immediately — no
  // retained stale display of the previous view's data under the new tab.
  useEffect(() => {
    seqRef.current += 1;
    activeRef.current = null;
    setActive(null);
    setRefreshError(null);
  }, [horizon, universe]);

  useEffect(() => {
    if (!candidateResults) return;
    const mySeq = ++seqRef.current;
    const viewHorizon = horizon;
    const viewUniverse = universe;

    if (candidateResults.available !== true) {
      // No per-stock race to close — a genuinely unavailable aggregate has
      // no per-stock data to mismatch against. Promote immediately.
      const snapshot: Snapshot = { horizon: viewHorizon, universe: viewUniverse, results: candidateResults, stockData: { available: false, stocks: [] } };
      activeRef.current = snapshot;
      setActive(snapshot);
      setRefreshError(null);
      return;
    }

    const candidateRunId = candidateResults.run_id;
    if (!isValidRunId(candidateRunId)) {
      // V-SNAP1D — FAIL CLOSED: no valid canonical run_id on the
      // aggregate candidate. This is indistinguishable from a pre-
      // V-SNAP1 backend, a Vercel/Railway deployment-skew window, or a
      // rollback — there is no way to safely pin a per-stock request, so
      // none is ever issued (never an unpinned "latest" request — that
      // is the exact race this whole phase exists to close). Never
      // promote, never fabricate a coherent snapshot.
      if (activeRef.current === null) {
        // Initial load with no valid identity: an honest, generic
        // unavailable state — never a verified snapshot, and no
        // technical implementation detail exposed.
        const snapshot: Snapshot = {
          horizon: viewHorizon, universe: viewUniverse,
          results: { available: false, message: "A consistent validation snapshot is temporarily unavailable." },
          stockData: { available: false, stocks: [] },
        };
        activeRef.current = snapshot;
        setActive(snapshot);
      } else {
        // A previously active, valid snapshot exists — keep it fully
        // intact and show a non-destructive refresh error instead.
        setRefreshError("Refresh failed — a consistent validation snapshot is temporarily unavailable.");
      }
      return;
    }

    const stocksUrl = `/api/validation/results/stocks?horizon=${viewHorizon}&universe=${viewUniverse}&run_id=${candidateRunId}`;

    api.get(stocksUrl).then(r => r.data as StockDataPayload).then((stockResp) => {
      if (seqRef.current !== mySeq) return; // superseded — never commit a stale promotion
      const identityMatches = stockResp?.available === true
        && isValidRunId(stockResp.run_id) && stockResp.run_id === candidateRunId;
      if (identityMatches) {
        const snapshot: Snapshot = { horizon: viewHorizon, universe: viewUniverse, results: candidateResults, stockData: stockResp };
        activeRef.current = snapshot;
        setActive(snapshot);
        setRefreshError(null);
      } else {
        // Per-stock refresh failed, or returned a missing/invalid/
        // mismatched run identity — keep whatever complete snapshot is
        // currently active and show an honest, non-destructive refresh
        // error instead of a mixed or unverified render.
        setRefreshError("Refresh failed — per-stock data for the latest run is unavailable. Showing the last complete snapshot.");
      }
    }).catch(() => {
      if (seqRef.current !== mySeq) return;
      setRefreshError("Refresh failed — per-stock data for the latest run is unavailable. Showing the last complete snapshot.");
    });
  }, [candidateResults, horizon, universe]);

  // V-SNAP1C — synchronous render-time coherence guard (second defense
  // layer): active horizon/universe must match the current selection, AND
  // the snapshot's own aggregate/per-stock run identity must agree. Any
  // failure here renders the existing loading/unavailable path — never a
  // silent substitution of mismatched or stale data.
  const showingSnapshot = active !== null
    && active.horizon === horizon
    && active.universe === universe
    && runIdsCoherent(active.results, active.stockData);
  const resultsLoading = !showingSnapshot && candidateLoading;
  const results = showingSnapshot ? active!.results : undefined;
  const stockData = showingSnapshot ? active!.stockData : undefined;

  // Reset per-stock pagination whenever the underlying dataset changes —
  // otherwise switching horizon/universe could leave "Load more" state
  // pointing past the new dataset's length.
  useEffect(() => {
    setStockPage(1);
  }, [horizon, universe]);

  const { data: status } = useQuery<RunStatus>({
    queryKey: ["validation-status"],
    queryFn: () => api.get("/api/validation/status").then(r => r.data),
    refetchInterval: (query) => query.state.data?.running ? 3000 : false,
    staleTime: 2000,
  });

  // V-SEC2: the public manual production-job trigger control (and its
  // mutation, which posted to the backend's job-start route) was removed
  // from this page entirely — that backend route now requires a header
  // credential (V-SEC1) which must never reach browser code. Manual
  // production validation runs remain possible only through that
  // protected backend route, invoked by an authorized operator with
  // server-side credentials — never from this page. Automatic scheduled
  // validation (internal, not HTTP-triggered) is unaffected.

  const res = results?.available ? results : null;
  const isRunning = status?.running === true;
  const benchmarkName = universe === "us" ? "S&P 500" : "Nifty";
  const universeLabel = UNIVERSES.find(u => u.key === universe)?.label ?? universe;

  // V-SCHED1C2D — whether the SELECTED horizon/universe is currently
  // auto-scheduled, derived entirely from the backend's own freshness
  // classification (never hardcoded "India = scheduled" here) —
  // res.freshness.reason is "calendar_or_completion_slo_unavailable" only
  // when services.validation_engine._short_universe_auto_scheduled found
  // this universe in the live VALIDATION_AUTO_SHORT_UNIVERSES allowlist;
  // it is "schedule_not_defined" otherwise. Requires res.horizon to
  // actually match the selected short tab so a stale/foreign-view result
  // (e.g. still showing a prior tab's data while the new one loads) can
  // never be misread as this tab's schedule state.
  const shortAutoScheduled =
    horizon === "short" && res?.horizon === "short" &&
    res?.freshness?.reason === "calendar_or_completion_slo_unavailable";

  // Selected view vs active running job: only render live progress under this
  // tab when the active job's identity actually matches the selection. A job
  // from another universe/horizon gets an explicit banner instead — never
  // relabeled as the selected view. Decision logic lives in
  // utils/validationJobView.ts (single decision point, directly unit-tested).
  const { showProgressForView, foreignJob } = resolveActiveJobView(status, universe, horizon);
  const jobMatchesView = showProgressForView && status?.job != null;
  const foreignJobLabel = foreignJob
    ? `${UNIVERSES.find(u => u.key === foreignJob.universe_id)?.label ?? foreignJob.universe_id} · ${foreignJob.horizon}`
    : null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <FlaskConical size={24} className="text-purple-400" />
            <h1 className="text-2xl font-bold">Model Validation</h1>
            <span className="text-xs bg-purple-500/15 text-purple-400 border border-purple-500/30 px-2 py-0.5 rounded-full font-semibold">
              Walk-Forward Backtest
            </span>
          </div>
          <p className="text-sm text-gray-400">
            Historical accuracy of the AI model across {universeLabel}. Validation is recomputed
            automatically — medium-horizon results are scheduled daily; long-horizon results are
            scheduled weekly on Sunday.
            {horizon === "short" && (
              <>
                {" "}
                {shortAutoScheduled
                  ? "Short-horizon results for this universe are scheduled automatically once per newly completed eligible exchange session. Eligibility is evaluated daily at 03:30 IST."
                  : "No automatic validation schedule is currently defined for this horizon and universe."}
              </>
            )}
          </p>
        </div>
      </div>

      {/* Warning banner */}
      <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3">
        <AlertCircle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
        <div className="text-sm text-yellow-300/80">
          <strong className="text-yellow-300">Walk-forward guarantee:</strong> At each historical date, the model only uses <em>price/technical</em> data
          available <em>before</em> that date — no look-ahead bias on price-derived scores. (Fundamentals are a separate exception —
          see the limitation notice below.) Correctness is <em>benchmark-relative</em>:{" "}
          a <strong className="text-yellow-300">BUY</strong> is correct only if the stock <em>outperforms</em> {benchmarkName} over the forward window;
          a <strong className="text-yellow-300">SELL</strong> is correct only if it <em>underperforms</em> {benchmarkName};
          a <strong className="text-yellow-300">HOLD</strong> is correct if it stays within ±threshold% of {benchmarkName}.{" "}
          "Avg BUY Return" and "Profitable BUY %" measure <em>absolute</em> return (not vs {benchmarkName}) — all other metrics are alpha-based.
        </div>
      </div>

      {/* V-SCHED1C2D — statistical-independence disclosure specific to Short:
          a 5-trading-day forward window recomputed once per newly completed
          session means consecutive short-horizon runs' evaluation windows
          overlap substantially. Adjacent to the walk-forward guarantee above,
          medium/long only (their 21/63-day windows and daily/weekly cadence
          don't share this same overlap concern in the same way). Describes
          the dependence honestly — does not claim it invalidates the results. */}
      {horizon === "short" && (
        <div
          data-testid="short-overlapping-window-disclosure"
          className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3"
        >
          <AlertCircle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
          <div className="text-sm text-yellow-300/80">
            Short-horizon accuracy statistics are recomputed for each eligible completed session
            using overlapping 5-trading-day forward windows. Consecutive runs therefore are not
            independent samples.
          </div>
        </div>
      )}

      {/* Universe selector */}
      <div className="flex gap-2">
        {UNIVERSES.map(({ key, label, sub }) => (
          <button
            key={key}
            onClick={() => setUniverse(key)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
              universe === key
                ? "bg-blue-600 text-white"
                : "bg-dark-card border border-dark-border text-gray-400 hover:text-white"
            }`}
          >
            {label}
            <span className="ml-1.5 text-xs opacity-60">({sub})</span>
          </button>
        ))}
      </div>

      {/* Horizon selector */}
      <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex gap-2">
            {HORIZONS.map(({ key, label, sub }) => (
              <button
                key={key}
                onClick={() => setHorizon(key)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                  horizon === key
                    ? "bg-purple-600 text-white"
                    : "bg-dark-border text-gray-400 hover:text-white"
                }`}
              >
                {label}
                <span className="ml-1.5 text-xs opacity-60">({sub})</span>
              </button>
            ))}
          </div>

          {/* V-SEC2: read-only progress indicator for an already-running scheduled
              job — never a control that starts one. Purely informational; driven
              by the same live /status poll the log panel below already uses. */}
          {isRunning && showProgressForView && (
            <span className="flex items-center gap-2 text-xs text-gray-400">
              <Loader2 size={14} className="animate-spin" /> Validation running… {status?.progress}/{status?.total}
            </span>
          )}
        </div>

        <p className="text-xs text-gray-600">
          Validation is recomputed automatically on a fixed internal schedule — this page cannot
          trigger a new run. Manual production runs are restricted to an authorized operator using a
          protected backend endpoint, never from this page.
        </p>

        {/* Active job from another universe/horizon — never rendered as this view's run */}
        {foreignJob && (
          <div
            data-testid="foreign-job-banner"
            className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-3 flex items-start gap-2 text-sm text-blue-300"
          >
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span>
              A <strong>{foreignJobLabel}</strong> validation is currently running
              ({foreignJob.processed ?? status?.progress}/{foreignJob.total ?? status?.total}).
              You are viewing <strong>{universeLabel} · {horizon}</strong> — results below are from
              the last completed run for this view.
            </span>
          </div>
        )}

        {/* Live log — only for a run that belongs to the selected view */}
        {(showProgressForView || (!isRunning && (status?.log?.length ?? 0) > 0 && (status?.job == null || jobMatchesView))) && (
          <div className="bg-black/40 rounded-lg p-3 max-h-36 overflow-y-auto font-mono text-xs text-gray-400 space-y-0.5">
            {(status?.log ?? []).slice(-20).map((line, i) => (
              <div key={i} className={line.startsWith("✅") ? "text-green-400" : line.startsWith("❌") ? "text-red-400" : ""}>{line}</div>
            ))}
            {showProgressForView && <div className="text-purple-400 animate-pulse">▋</div>}
          </div>
        )}
      </div>

      {/* No results yet — a foreign-universe run doesn't suppress this view's empty state */}
      {!res && !resultsLoading && !showProgressForView && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <BarChart3 size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">No validation results yet for {horizon} horizon · {universeLabel}</h3>
          <p className="text-sm text-gray-500 max-w-sm">
            Results will appear here after the next automatic validation run completes.
          </p>
        </div>
      )}

      {resultsLoading && (
        <div className="flex items-center gap-3 text-gray-400 py-8">
          <Loader2 size={18} className="animate-spin" /> Loading results…
        </div>
      )}

      {/* Results */}
      {res && (
        <>
          {/* Run metadata */}
          <div className="flex items-center gap-4 text-xs text-gray-500 flex-wrap">
            <span>Stocks tested: <strong className="text-gray-300">{res.n_stocks_tested}</strong></span>
            <span>Total signals: <strong className="text-gray-300">{res.total_signals?.toLocaleString()}</strong></span>
            <span>BUY signals: <strong className="text-gray-300">{res.buy_signals?.toLocaleString()}</strong></span>
            <span>Validation completed: <strong className="text-gray-300">
              {res.run_at ? new Date(res.run_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
            </strong></span>
            <button
              onClick={() => {
                // V-SNAP1B: this only invalidates the CANDIDATE aggregate
                // fetch. The pinned per-stock fetch and atomic promotion are
                // driven entirely by the candidate-arrival effect above —
                // the currently active, complete snapshot stays visible
                // until a new candidate is fully verified.
                qc.invalidateQueries({ queryKey: ["validation-results", horizon] });
              }}
              className="flex items-center gap-1 text-gray-500 hover:text-white transition-colors"
              aria-label="Refresh displayed results"
            >
              <RefreshCw size={11} /> Refresh displayed results
            </button>
          </div>
          {refreshError && (
            <div
              data-testid="refresh-error-banner"
              className="flex items-start gap-2 text-xs text-yellow-400 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-3 py-2 -mt-2"
            >
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              <span>{refreshError}</span>
            </div>
          )}
          <p className="text-xs text-gray-600 -mt-2">
            This is the last successful validation completion for the selected horizon and universe —
            not necessarily the underlying market data&apos;s own freshness.
          </p>

          {/* V-FRESH1B — Option A: a durable metadata-and-disclosure
              foundation, NOT a complete fresh/stale classifier. No
              exchange-calendar utility and no completion-SLO contract
              exist yet, so this is always neutral/unknown in this phase —
              never styled red/green, never worded as "fresh" or "stale".
              Available data remains fully visible above regardless. */}
          {res.freshness && (
            <div
              data-testid="freshness-disclosure"
              className="rounded-lg border border-dark-border bg-dark-card/50 px-3 py-2.5 space-y-1.5"
            >
              <p className="text-xs font-semibold text-gray-300">Freshness not yet verifiable</p>
              <p className="text-xs text-gray-500">
                {res.freshness.reason === "schedule_not_defined" &&
                  "No automatic validation schedule is currently defined for this horizon and universe."}
                {res.freshness.reason === "legacy_run_without_evidence_metadata" &&
                  "This stored run predates durable input and outcome evidence metadata."}
                {/* V-SCHED1C2D — calendar_or_completion_slo_unavailable is shared by
                    two genuinely different situations: medium/long (no automatic-short
                    schedule concept applies at all — the original wording, unchanged)
                    vs. an auto-scheduled short universe (nifty100/midcap today — a real
                    schedule exists, but end-to-end completion-SLO freshness still can't
                    be independently verified). Never claim the short scheduler's own
                    market-calendar resolution is "unavailable" here — it has a real
                    exchange calendar (see services/market_calendar.py); only the
                    completion-SLO/grace-period judgment is what's still missing. */}
                {res.freshness.reason === "calendar_or_completion_slo_unavailable" && res.horizon === "short" &&
                  "Automatic scheduling is active for this universe, but end-to-end completion-SLO freshness is not yet independently verifiable."}
                {res.freshness.reason === "calendar_or_completion_slo_unavailable" && res.horizon !== "short" &&
                  "Input and outcome dates are recorded, but market-calendar-aware freshness is not yet available."}
              </p>
              {res.validation_evidence && (
                <div className="text-xs text-gray-500 space-y-1 pt-1">
                  {(res.validation_evidence.input_latest_bar_date_min || res.validation_evidence.input_latest_bar_date_max) && (
                    <p>
                      Latest input bars across{" "}
                      <span className="text-gray-400">{res.validation_evidence.symbols_with_input_data}</span> symbols
                      ranged from{" "}
                      <span className="text-gray-400">{res.validation_evidence.input_latest_bar_date_min ?? "—"}</span> to{" "}
                      <span className="text-gray-400">{res.validation_evidence.input_latest_bar_date_max ?? "—"}</span>.
                    </p>
                  )}
                  {(res.validation_evidence.signal_date_min || res.validation_evidence.signal_date_max) && (
                    <p>
                      Signals in this run span{" "}
                      <span className="text-gray-400">{res.validation_evidence.signal_date_min ?? "—"}</span> to{" "}
                      <span className="text-gray-400">{res.validation_evidence.signal_date_max ?? "—"}</span>{" "}
                      (signal dates, not input-data-through).
                    </p>
                  )}
                  {(res.validation_evidence.evaluated_exit_date_min || res.validation_evidence.evaluated_exit_date_max) && (
                    <p>
                      Evaluated outcomes use exit bars from{" "}
                      <span className="text-gray-400">{res.validation_evidence.evaluated_exit_date_min ?? "—"}</span> to{" "}
                      <span className="text-gray-400">{res.validation_evidence.evaluated_exit_date_max ?? "—"}</span>.
                    </p>
                  )}
                  <p>
                    {res.validation_evidence.symbols_requested} requested ·{" "}
                    {res.validation_evidence.symbols_with_input_data} returned price data ·{" "}
                    {res.validation_evidence.symbols_with_sufficient_data} had sufficient history ·{" "}
                    {res.validation_evidence.symbols_processed} were processed ·{" "}
                    {res.validation_evidence.symbols_with_signals} produced signals ·{" "}
                    {res.validation_evidence.symbols_with_evaluated_outcomes} produced evaluated outcomes
                  </p>
                  <details className="pt-0.5">
                    <summary className="cursor-pointer text-gray-600 hover:text-gray-400">
                      Diagnostic detail
                    </summary>
                    <p className="pt-1 text-gray-600">
                      Fetch exceptions: {res.validation_evidence.fetch_exception_count} · Empty data:{" "}
                      {res.validation_evidence.empty_data_count} · Insufficient data:{" "}
                      {res.validation_evidence.insufficient_data_count} · Calculation exceptions:{" "}
                      {res.validation_evidence.calculation_exception_count}
                    </p>
                  </details>
                </div>
              )}
            </div>
          )}

          {/* DP-026 — always rendered, not conditioned on the API's
              data_limitations field (legacy runs predate it but carry the
              same limitation). The shared component's copy is written for
              the India case (still not point-in-time) and is intentionally
              left unmodified here — it's reused on other pages beyond this
              remediation's scope. For US, where THIS RUN's own
              data_limitations disclosure (a field the backend computes per
              run from its actual stored fund_pit_available/scoring-version
              evidence, not a hardcoded claim) reports genuine point-in-time
              fundamentals, we add an accurate market-specific correction
              directly below it rather than editing the shared copy. */}
          <DataLimitationsNotice />
          {universe === "us" && res.data_limitations?.fundamentals_point_in_time === true && (
            <div className="flex items-start gap-2 rounded-lg border border-green-500/25 bg-green-500/[0.06] px-3 py-2">
              <CheckCircle2 size={14} className="text-green-300 mt-0.5 shrink-0" />
              <p className="text-[11px] leading-relaxed text-green-200/90">
                <strong className="text-green-200">US correction:</strong> unlike the notice above
                (written for India, which is not yet point-in-time), US fundamentals in this run ARE
                reconstructed as-of each historical signal date from persisted SEC EDGAR filings
                {res.data_limitations.fundamentals_point_in_time_coverage_pct != null && (
                  <> — {res.data_limitations.fundamentals_point_in_time_coverage_pct}% of signals
                  found an eligible filing; the remainder used a disclosed neutral fallback</>
                )}. This is a separately-versioned scoring formula (ROE + net margin only), not
                equivalent to the legacy PE+ROE+revenue-growth formula India still uses.
              </p>
            </div>
          )}

          {/* Primary metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label={`BUY Hit Rate (vs ${benchmarkName})`}
              value={res.buy_hit_rate_pct != null ? `${res.buy_hit_rate_pct}%` : null}
              sub={(() => {
                const base = `% of BUY calls that beat ${benchmarkName}`;
                // Exact evaluated n and hit count — never buy_signals (the
                // total predicted-BUY count) and never a hit count
                // reconstructed from the rounded displayed percentage.
                if (res.buy_hits == null || !res.evaluated_buy_count) return base;
                const ci = wilson95(res.buy_hits, res.evaluated_buy_count);
                const nSuffix = ` (n=${res.evaluated_buy_count.toLocaleString()} evaluated)`;
                return ci ? `${base} · 95% CI [${ci.lo}%, ${ci.hi}%]${nSuffix}` : `${base}${nSuffix}`;
              })()}
              color={
                (res.buy_hit_rate_pct ?? 0) >= 60 ? "text-green-400" :
                (res.buy_hit_rate_pct ?? 0) >= 53 ? "text-yellow-400" : "text-red-400"
              }
              icon={<Target size={14} />}
            />
            <StatCard
              label="Avg Alpha on BUY"
              value={res.avg_alpha_on_buy_pct != null ? `${res.avg_alpha_on_buy_pct > 0 ? "+" : ""}${res.avg_alpha_on_buy_pct}%` : null}
              sub={`Mean outperformance vs ${benchmarkName} per call`}
              color={(res.avg_alpha_on_buy_pct ?? 0) > 0 ? "text-green-400" : "text-red-400"}
              icon={<TrendingUp size={14} />}
            />
            <StatCard
              label="Strong Alpha %"
              value={res.beat_benchmark_pct != null ? `${res.beat_benchmark_pct}%` : null}
              sub={`% of BUY calls with alpha > 1% vs ${benchmarkName}`}
              color={
                (res.beat_benchmark_pct ?? 0) >= 50 ? "text-green-400" :
                (res.beat_benchmark_pct ?? 0) >= 38 ? "text-yellow-400" : "text-red-400"
              }
              icon={<Zap size={14} />}
            />
            <StatCard
              label="Sharpe on Alphas"
              value={res.sharpe_on_alphas != null ? res.sharpe_on_alphas.toFixed(2) : null}
              sub=">1.0 = good risk-adjusted alpha"
              color={
                (res.sharpe_on_alphas ?? 0) >= 1.0 ? "text-green-400" :
                (res.sharpe_on_alphas ?? 0) >= 0.5 ? "text-yellow-400" : "text-red-400"
              }
              icon={<BarChart3 size={14} />}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Avg BUY Return"
              value={res.avg_return_on_buy_pct != null ? `${res.avg_return_on_buy_pct > 0 ? "+" : ""}${res.avg_return_on_buy_pct}%` : null}
              sub={`Absolute return · ${benchmarkName} avg: ${res.nifty_avg_fwd_return_pct ?? "—"}%`}
              color={(res.avg_return_on_buy_pct ?? 0) > (res.nifty_avg_fwd_return_pct ?? 0) ? "text-green-400" : "text-yellow-400"}
            />
            <StatCard
              label="Profitable BUY %"
              value={res.profitable_buy_pct != null ? `${res.profitable_buy_pct}%` : null}
              sub={`% with positive absolute return (not vs ${benchmarkName})`}
              color={(res.profitable_buy_pct ?? 0) >= 55 ? "text-green-400" : "text-yellow-400"}
            />
            <StatCard
              label="SELL Hit Rate"
              value={res.sell_hit_rate_pct != null ? `${res.sell_hit_rate_pct}%` : null}
              sub="% of SELL calls that underperformed"
              color={
                (res.sell_hit_rate_pct ?? 0) >= 53 ? "text-green-400" :
                (res.sell_hit_rate_pct ?? 0) >= 45 ? "text-yellow-400" : "text-red-400"
              }
              icon={<TrendingDown size={14} />}
            />
            <StatCard
              label="Overall Accuracy"
              value={res.overall_accuracy_pct != null ? `${res.overall_accuracy_pct}%` : null}
              // V-VAL1 correction: buy_signals/sell_signals/hold_signals are
              // PREDICTED-class counts (what the model called), not an
              // actual/ground-truth class distribution — max(predicted
              // counts)/total is the model's prediction MIX, not a valid
              // majority-class accuracy baseline (that requires the
              // distribution of actual labels, which this response does
              // not expose). Do not reintroduce that calculation without a
              // genuine actual-label source backing it.
              sub="Accuracy across evaluated BUY, SELL and HOLD predictions. This summary does not currently expose an actual-class distribution for a naïve majority-label baseline."
              color="text-white"
            />
          </div>

          {/* Statistical significance + drawdown */}
          {(() => {
            // Exact evaluated evidence only — never total buy_signals,
            // never a hit count reconstructed from a rounded percentage.
            // Section 3: an unavailable sample (evaluated_buy_count === 0,
            // or buy_hits missing) must render an EXPLICIT unavailable
            // state, not be silently hidden.
            const evidenceKnown = res.evaluated_buy_count != null;
            const hasEvidence = res.buy_hits != null && !!res.evaluated_buy_count;
            const pv = hasEvidence ? exactBinomialTwoSidedPValue(res.buy_hits!, res.evaluated_buy_count!) : null;
            const smallSample = !!res.evaluated_buy_count && res.evaluated_buy_count < 30;
            return (
              <div className="grid md:grid-cols-2 gap-4">
                {/* P-value card */}
                {evidenceKnown && (
                  <div className={`rounded-xl border p-4 ${!pv ? "bg-dark-card border-dark-border" : pv.significant ? "bg-green-500/10 border-green-500/30" : "bg-yellow-500/10 border-yellow-500/30"}`}>
                    <div className="flex items-center gap-2 mb-2">
                      {pv ? (
                        pv.significant
                          ? <CheckCircle2 size={15} className="text-green-400" />
                          : <AlertCircle size={15} className="text-yellow-400" />
                      ) : <AlertCircle size={15} className="text-gray-500" />}
                      {/* V-VAL1 correction: scoped away from the broad,
                          unqualified "Statistical Significance" title — this
                          card tests exactly one metric (the evaluated BUY
                          hit rate vs. a 50% null), not the model. Never
                          rename this back to a broad "validated"/"proven"-
                          style title without the same scoping this comment
                          documents. */}
                      <p className="text-xs font-semibold text-gray-300">BUY Hit-Rate Binomial Check</p>
                    </div>
                    {pv ? (
                      <>
                        <p className={`text-2xl font-bold font-mono mb-1 ${pv.significant ? "text-green-400" : "text-yellow-400"}`}>
                          p = {pv.p < 0.001 ? "<0.001" : pv.p.toFixed(3)}
                        </p>
                        <p className="text-xs text-gray-400">
                          {res.buy_hits!.toLocaleString()}/{res.evaluated_buy_count!.toLocaleString()} evaluated BUY
                          signals (n) · p₀ = 50% · two-sided
                          {smallSample && <span className="text-yellow-500"> · small sample</span>}
                        </p>
                        <p className="text-xs mt-1.5 text-gray-400">
                          Exact two-sided binomial check of evaluated BUY outcomes against a 50% null.
                          Unadjusted — historical signals can overlap and be correlated by stock, date,
                          holding window and market regime, so the independent-trial assumption behind
                          this p-value is not established, and repeated checks across markets, horizons
                          or score buckets carry multiple-testing risk this single number does not
                          account for. Indicative/descriptive evidence only — not confirmatory proof of
                          model skill, and a statistical difference here does not establish economic
                          value, future profitability, or that the AI Score is calibrated.
                        </p>
                        <p className="text-xs mt-1.5">
                          {pv.significant
                            ? <span className="text-green-400">Below the conventional 0.05 threshold for this unadjusted BUY-outcome check.</span>
                            : <span className="text-yellow-400">Not below the conventional 0.05 threshold for this unadjusted BUY-outcome check.</span>}
                        </p>
                      </>
                    ) : (
                      <p className="text-sm text-gray-500">
                        {res.evaluated_buy_count === 0
                          ? "Unavailable — no evaluated BUY signals in this sample."
                          : "Unavailable — malformed evaluated-BUY evidence."}
                      </p>
                    )}
                  </div>
                )}
                {/* Drawdown / streak card */}
                {(res.max_drawdown_pct != null || res.max_consecutive_wrong != null) && (
                  <div className="rounded-xl border border-dark-border bg-dark-card p-4 space-y-3">
                    <p className="text-xs font-semibold text-gray-300">Worst-Case Analysis (BUY signals)</p>
                    <div className="grid grid-cols-3 gap-3">
                      {res.max_drawdown_pct != null && (
                        <div>
                          <p className="text-xs text-gray-500 mb-0.5">Max Drawdown<sup>†</sup></p>
                          <p className={`text-lg font-bold font-mono ${res.max_drawdown_pct > 25 ? "text-red-400" : res.max_drawdown_pct > 15 ? "text-yellow-400" : "text-green-400"}`}>
                            -{res.max_drawdown_pct}%
                          </p>
                          <p className="text-xs text-gray-400">equal-weight signal-date return curve</p>
                        </div>
                      )}
                      {res.max_consecutive_wrong != null && (
                        <div>
                          <p className="text-xs text-gray-500 mb-0.5">Worst Streak</p>
                          <p className={`text-lg font-bold font-mono ${res.max_consecutive_wrong > 8 ? "text-red-400" : res.max_consecutive_wrong > 5 ? "text-yellow-400" : "text-green-400"}`}>
                            {res.max_consecutive_wrong} wrong
                          </p>
                          <p className="text-xs text-gray-400">consecutive</p>
                        </div>
                      )}
                      {res.max_consecutive_right != null && (
                        <div>
                          <p className="text-xs text-gray-500 mb-0.5">Best Streak</p>
                          <p className="text-lg font-bold font-mono text-green-400">
                            {res.max_consecutive_right} right
                          </p>
                          <p className="text-xs text-gray-400">consecutive</p>
                        </div>
                      )}
                    </div>
                    {res.max_drawdown_pct != null && (
                      <p className="text-xs text-gray-600">
                        † Peak-to-trough on the equal-weight mean BUY return per signal-date,
                        compounded chronologically — NOT a capital-allocated portfolio. Multiple BUY
                        signals on the same historical date (the common case) are averaged into one
                        date-level return rather than compounded sequentially, so the result does not
                        depend on which signal happened to be processed first; it still makes no
                        capital-allocation, transaction-cost, or execution-timing assumption.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* Score bucket table */}
          {res.score_buckets && res.score_buckets.length > 0 && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Signal Performance by AI Score</p>
              <p className="text-xs text-gray-500 mb-4">
                Among BUY signals in each composite AI-score range, what % beat the {benchmarkName} benchmark?
                This score is a raw composite ranking input, not a calibrated probability — this table
                is a descriptive breakdown, not proof the score is well-calibrated. Rows below{" "}
                {MIN_RELIABLE_SCORE_BUCKET_N} signals (dimmed, marked *) are too small to draw a
                conclusion from; among the adequately-sampled rows, a genuinely informative score
                would show hit rate rising with score — if it doesn&apos;t, that&apos;s evidence the
                score isn&apos;t reliably separating outcomes in this sample, not a display bug.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-dark-border">
                      <th className="text-left py-2 pr-4">AI Score</th>
                      <th className="text-right py-2 pr-4">Signals</th>
                      <th className="text-right py-2 pr-4">Hit Rate</th>
                      <th className="text-right py-2">Avg Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.score_buckets.map((b) => {
                      const lowN = b.count < MIN_RELIABLE_SCORE_BUCKET_N;
                      const hr = b.hit_rate_pct ?? 0;
                      const hrColor = lowN ? "text-gray-500" : hr >= 60 ? "text-green-400" : hr >= 52 ? "text-yellow-400" : "text-red-400";
                      const retColor = lowN ? "text-gray-500" : (b.avg_return_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400";
                      return (
                        <tr key={b.score_range} className={`border-b border-dark-border/50 hover:bg-dark-border/10 ${lowN ? "opacity-60" : ""}`}>
                          <td className="py-2.5 pr-4 font-mono text-white font-semibold">
                            {b.score_range}{lowN && <span className="text-gray-500">*</span>}
                          </td>
                          <td className="text-right pr-4 text-gray-400">{b.count}</td>
                          <td className={`text-right pr-4 font-semibold ${hrColor}`}>
                            {b.hit_rate_pct != null ? `${b.hit_rate_pct}%` : "—"}
                          </td>
                          <td className={`text-right font-semibold ${retColor}`}>
                            {b.avg_return_pct != null
                              ? `${b.avg_return_pct > 0 ? "+" : ""}${b.avg_return_pct}%`
                              : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {res.score_buckets.some(b => b.count < MIN_RELIABLE_SCORE_BUCKET_N) && (
                <p className="text-xs text-gray-600 mt-2">
                  * fewer than {MIN_RELIABLE_SCORE_BUCKET_N} signals — limited sample, interpret cautiously
                </p>
              )}
            </div>
          )}

          {/* Factor IC */}
          {res.factor_ic && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Factor Information Coefficients</p>
              <p className="text-xs text-gray-500 mb-2">
                IC = Pearson correlation between each factor&apos;s score and actual forward return
                across all signals (BUY + SELL + HOLD) at this horizon, computed only when at least
                30 valid score/return pairs exist for that factor (otherwise shown as unavailable).
                IC &gt; 0.05 = meaningful by convention. IC ≈ 0 = noise. Negative = contrarian.
              </p>
              <p className="text-xs text-gray-500 mb-4">
                <strong className="text-gray-400">IC vs. the score table above are different tests:</strong>{" "}
                IC measures whether the score linearly <em>ranks</em> continuous forward returns across
                every signal; the score table above measures a binary <em>beat-the-benchmark</em> hit
                rate only for BUY signals above the 60-point threshold. A near-zero Composite Score IC
                does not contradict a rising hit-rate pattern in the table — they can genuinely diverge —
                but it does mean the composite score should not be read as reliably ranking future
                returns. The Composite Score (unlike the four factors below it) includes a fundamentals
                sub-score; for India, that sub-score reuses a current-day snapshot rather than a
                point-in-time reconstruction (see the fundamentals limitation notice above), so
                Composite Score IC for India may be affected by that same look-ahead exposure — the four
                price/technical factors below are not.
              </p>
              <div className="space-y-4">
                <ICBar label="Composite Score"                    value={res.factor_ic.composite} color="bg-purple-500" />
                <ICBar label="Technical (RSI/MACD/EMA/ADX/BB)"   value={res.factor_ic.tech}      color="bg-blue-500" />
                <ICBar label={`Relative Strength vs ${benchmarkName}`}         value={res.factor_ic.rs}        color="bg-green-500" />
                <ICBar label="OBV Trend (Volume Flow)"            value={res.factor_ic.obv}       color="bg-yellow-500" />
                <ICBar label="MFI (Money Flow Index)"             value={res.factor_ic.mfi}       color="bg-orange-500" />
              </div>
            </div>
          )}

          {/* Per-stock table */}
          {stockData?.available === false && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5 text-xs text-gray-500">
              Per-stock validation data is temporarily unavailable — this is not the same as zero BUY signals.
            </div>
          )}
          {stockData?.stocks && stockData.available !== false && stockData.stocks.length > 0 && (() => {
            // Stable sort order: backend ORDER BY buy_avg_ret DESC NULLS LAST
            // (services/validation_engine.py get_per_stock_results) — highest
            // average BUY return first. Pagination below preserves that order.
            const withBuys = stockData.stocks.filter(s => s.buy_signal_count > 0);
            const visibleTo = Math.min(stockPage * PER_STOCK_PAGE_SIZE, withBuys.length);
            const shown = withBuys.slice(0, visibleTo);
            const hasMore = visibleTo < withBuys.length;
            return (
              <div className="bg-dark-card border border-dark-border rounded-xl p-5">
                <p className="text-sm font-semibold text-white mb-1">
                  Per-Stock Results — showing 1–{shown.length} of {withBuys.length} stocks with
                  BUY signals, sorted by avg. BUY return (highest first)
                </p>
                <p className="text-xs text-gray-500 mb-4">
                  Rows with fewer than {MIN_RELIABLE_STOCK_BUY_N} evaluated BUY signals (dimmed,
                  marked *) are a limited sample for a per-stock hit rate — a single evaluated
                  signal can only be 0% or 100%.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-gray-500 border-b border-dark-border">
                        <th className="text-left py-2 pr-3">Symbol</th>
                        <th className="text-right pr-3">BUY signals</th>
                        <th className="text-right pr-3">Hit Rate</th>
                        <th className="text-right">Avg BUY Return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((s) => {
                        const lowN = s.evaluated_buy_count < MIN_RELIABLE_STOCK_BUY_N;
                        return (
                          <tr key={s.symbol} className={`border-b border-dark-border/30 hover:bg-dark-border/10 ${lowN ? "opacity-60" : ""}`}>
                            <td className="py-2 pr-3 font-mono font-semibold text-white">
                              {s.symbol}{lowN && <span className="text-gray-500" aria-label="limited sample">*</span>}
                            </td>
                            <td className="text-right pr-3 text-gray-400">
                              {s.evaluated_buy_count}
                              {s.evaluated_buy_count !== s.buy_signal_count && (
                                <span className="text-gray-600"> / {s.buy_signal_count}</span>
                              )}
                            </td>
                            <td className={`text-right pr-3 font-semibold ${lowN ? "text-gray-500" : (s.hit_rate_pct ?? 0) >= 60 ? "text-green-400" : (s.hit_rate_pct ?? 0) >= 50 ? "text-yellow-400" : "text-red-400"}`}>
                              {s.hit_rate_pct != null ? `${s.hit_rate_pct}%` : "unavailable"}
                            </td>
                            <td className={`text-right font-semibold ${lowN ? "text-gray-500" : (s.buy_avg_return_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {s.buy_avg_return_pct != null
                                ? `${s.buy_avg_return_pct > 0 ? "+" : ""}${s.buy_avg_return_pct}%`
                                : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {hasMore && (
                  <button
                    onClick={() => setStockPage(p => p + 1)}
                    className="mt-4 w-full py-2 rounded-lg text-sm font-medium bg-dark-border text-gray-300 hover:text-white hover:bg-dark-border/70 transition-colors"
                  >
                    Load more ({withBuys.length - visibleTo} remaining)
                  </button>
                )}
              </div>
            );
          })()}

          {/* How to interpret */}
          <div className="bg-dark-card border border-dark-border rounded-xl p-5">
            <p className="text-sm font-semibold text-white mb-3">How to Interpret These Results</p>
            <div className="grid md:grid-cols-2 gap-4 text-xs text-gray-400">
              <div className="space-y-2">
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Strong Alpha &gt; 50%</strong> — more than half of BUY calls beat {benchmarkName} by &gt;1%, showing genuine edge.</p>
                </div>
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Avg alpha &gt; 1%</strong> — meaningful return above {benchmarkName} per trade.</p>
                </div>
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Sharpe on alphas &gt; 1.0</strong> — excess return is worth the risk.</p>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-start gap-2">
                  <XCircle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Strong Alpha &lt; 38%</strong> — fewer than 2-in-5 BUY calls generate meaningful outperformance.</p>
                </div>
                <div className="flex items-start gap-2">
                  <XCircle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Factor IC near zero</strong> — that sub-score is pure noise; consider removing it.</p>
                </div>
                <div className="flex items-start gap-2">
                  <AlertCircle size={14} className="text-yellow-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Backtest ≠ guarantee</strong> — past accuracy doesn&apos;t guarantee future results. Always do your own research.</p>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
