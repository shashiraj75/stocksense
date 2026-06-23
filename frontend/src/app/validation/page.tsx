"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/utils/api";
import {
  FlaskConical, TrendingUp, TrendingDown, Target, Zap,
  BarChart3, CheckCircle2, XCircle, AlertCircle, Loader2, Play, RefreshCw,
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
  n_stocks_tested?: number;
  run_at?: string;
  total_signals?: number;
  buy_signals?: number;
  sell_signals?: number;
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
};

type StockResult = {
  symbol: string;
  total_signals: number;
  correct: number;
  hit_rate_pct: number;
  avg_fwd_return_pct: number | null;
  buy_avg_return_pct: number | null;
  buy_signal_count: number;
};

type RunStatus = {
  running: boolean;
  progress: number;
  total: number;
  started_at: string | null;
  log: string[];
};

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
          ? "✅ Statistically meaningful"
          : Math.abs(value) > 0.02
          ? "🟡 Weak signal"
          : "❌ Near zero — noise"}
      </p>
    </div>
  );
}

// Normal approximation to binomial CDF (one-tailed p-value for hit rate > 50%)
function binomialPValue(n: number, hitRatePct: number): { p: number; z: number; significant: boolean } | null {
  if (!n || n < 30) return null;
  const k = (hitRatePct / 100) * n;
  const z = (k - n * 0.5) / Math.sqrt(n * 0.25);
  // Approximation of upper-tail p from z-score
  const p = (() => {
    const t = 1 / (1 + 0.2316419 * Math.abs(z));
    const poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
    const pNorm = 1 - (1 / Math.sqrt(2 * Math.PI)) * Math.exp(-z * z / 2) * poly;
    return z >= 0 ? 1 - pNorm : pNorm;
  })();
  return { p: Math.max(0.0001, p), z: Math.round(z * 100) / 100, significant: p < 0.05 };
}

export default function ValidationPage() {
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("medium");
  const [universe, setUniverse] = useState<"nifty100" | "midcap" | "us">("nifty100");
  const qc = useQueryClient();

  const { data: results, isLoading: resultsLoading } = useQuery<ValidationResult>({
    queryKey: ["validation-results", horizon, universe],
    queryFn: () => api.get(`/api/validation/results?horizon=${horizon}&universe=${universe}`).then(r => r.data),
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const { data: stockData } = useQuery<{ stocks: StockResult[] }>({
    queryKey: ["validation-stocks", horizon, universe],
    queryFn: () => api.get(`/api/validation/results/stocks?horizon=${horizon}&universe=${universe}`).then(r => r.data),
    enabled: results?.available === true,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const { data: status } = useQuery<RunStatus>({
    queryKey: ["validation-status"],
    queryFn: () => api.get("/api/validation/status").then(r => r.data),
    refetchInterval: (query) => query.state.data?.running ? 3000 : false,
    staleTime: 2000,
  });

  const { mutate: triggerRun, isPending: triggering } = useMutation({
    mutationFn: () => api.post(`/api/validation/run?horizon=${horizon}&universe=${universe}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["validation-status"] });
      qc.invalidateQueries({ queryKey: ["validation-results"] });
    },
  });

  const res = results?.available ? results : null;
  const isRunning = status?.running === true;
  const benchmarkName = universe === "us" ? "S&P 500" : "Nifty";
  const universeLabel = UNIVERSES.find(u => u.key === universe)?.label ?? universe;

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
            Historical accuracy of the AI model across {universeLabel}. Runs automatically every Sunday.
          </p>
        </div>
      </div>

      {/* Warning banner */}
      <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3">
        <AlertCircle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
        <div className="text-sm text-yellow-300/80">
          <strong className="text-yellow-300">Walk-forward guarantee:</strong> At each historical date, the model only uses data
          available <em>before</em> that date — no look-ahead bias. Correctness is <em>benchmark-relative</em>:{" "}
          a <strong className="text-yellow-300">BUY</strong> is correct only if the stock <em>outperforms</em> {benchmarkName} over the forward window;
          a <strong className="text-yellow-300">SELL</strong> is correct only if it <em>underperforms</em> {benchmarkName};
          a <strong className="text-yellow-300">HOLD</strong> is correct if it stays within ±threshold% of {benchmarkName}.{" "}
          "Avg BUY Return" and "Profitable BUY %" measure <em>absolute</em> return (not vs {benchmarkName}) — all other metrics are alpha-based.
        </div>
      </div>

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

      {/* Horizon selector + run controls */}
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

          <button
            onClick={() => triggerRun()}
            disabled={isRunning || triggering}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-semibold transition-colors ml-auto"
          >
            {isRunning
              ? <><Loader2 size={14} className="animate-spin" /> Running… {status?.progress}/{status?.total}</>
              : <><Play size={14} /> Run Now ({universeLabel} · {horizon})</>
            }
          </button>
        </div>

        <p className="text-xs text-gray-600">
          Medium-horizon runs every day at 6:00 AM IST. Long-horizon runs every Sunday at 6:00 AM IST.
          Use "Run Now" to trigger on-demand — ~15 min for medium, ~25 min for long.
        </p>

        {/* Live log */}
        {(isRunning || (status?.log?.length ?? 0) > 0) && (
          <div className="bg-black/40 rounded-lg p-3 max-h-36 overflow-y-auto font-mono text-xs text-gray-400 space-y-0.5">
            {(status?.log ?? []).slice(-20).map((line, i) => (
              <div key={i} className={line.startsWith("✅") ? "text-green-400" : line.startsWith("❌") ? "text-red-400" : ""}>{line}</div>
            ))}
            {isRunning && <div className="text-purple-400 animate-pulse">▋</div>}
          </div>
        )}
      </div>

      {/* No results yet */}
      {!res && !resultsLoading && !isRunning && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <BarChart3 size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">No validation results yet for {horizon} horizon · {universeLabel}</h3>
          <p className="text-sm text-gray-500 max-w-sm">
            Click "Run Now" to start, or wait for the automatic Sunday run.
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
            <span>Last run: <strong className="text-gray-300">
              {res.run_at ? new Date(res.run_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
            </strong></span>
            <button
              onClick={() => {
                qc.invalidateQueries({ queryKey: ["validation-results", horizon] });
                qc.invalidateQueries({ queryKey: ["validation-stocks", horizon] });
              }}
              className="flex items-center gap-1 text-gray-500 hover:text-white transition-colors"
            >
              <RefreshCw size={11} /> refresh
            </button>
          </div>

          {/* Primary metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label={`BUY Hit Rate (vs ${benchmarkName})`}
              value={res.buy_hit_rate_pct != null ? `${res.buy_hit_rate_pct}%` : null}
              sub={`% of BUY calls that beat ${benchmarkName}`}
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
              sub="All signals (BUY + SELL + HOLD)"
              color={
                (res.overall_accuracy_pct ?? 0) >= 58 ? "text-green-400" :
                (res.overall_accuracy_pct ?? 0) >= 52 ? "text-yellow-400" : "text-red-400"
              }
            />
          </div>

          {/* Statistical significance + drawdown */}
          {(() => {
            const pv = res.buy_signals && res.buy_hit_rate_pct != null
              ? binomialPValue(res.buy_signals, res.buy_hit_rate_pct)
              : null;
            return (
              <div className="grid md:grid-cols-2 gap-4">
                {/* P-value card */}
                {pv && (
                  <div className={`rounded-xl border p-4 ${pv.significant ? "bg-green-500/10 border-green-500/30" : "bg-yellow-500/10 border-yellow-500/30"}`}>
                    <div className="flex items-center gap-2 mb-2">
                      {pv.significant
                        ? <CheckCircle2 size={15} className="text-green-400" />
                        : <AlertCircle size={15} className="text-yellow-400" />}
                      <p className="text-xs font-semibold text-gray-300">Statistical Significance</p>
                    </div>
                    <p className={`text-2xl font-bold font-mono mb-1 ${pv.significant ? "text-green-400" : "text-yellow-400"}`}>
                      p = {pv.p < 0.001 ? "<0.001" : pv.p.toFixed(3)}
                    </p>
                    <p className="text-xs text-gray-400">
                      z-score: {pv.z} · n = {res.buy_signals?.toLocaleString()} BUY signals
                    </p>
                    <p className="text-xs mt-1.5">
                      {pv.significant
                        ? <span className="text-green-400">✅ Hit rate is statistically better than random (p &lt; 0.05)</span>
                        : <span className="text-yellow-400">⚠️ Hit rate is not yet statistically significant — need more signals</span>}
                    </p>
                  </div>
                )}
                {/* Drawdown / streak card */}
                {(res.max_drawdown_pct != null || res.max_consecutive_wrong != null) && (
                  <div className="rounded-xl border border-dark-border bg-dark-card p-4 space-y-3">
                    <p className="text-xs font-semibold text-gray-300">Worst-Case Analysis (BUY signals)</p>
                    <div className="grid grid-cols-3 gap-3">
                      {res.max_drawdown_pct != null && (
                        <div>
                          <p className="text-xs text-gray-500 mb-0.5">Max Drawdown</p>
                          <p className={`text-lg font-bold font-mono ${res.max_drawdown_pct > 25 ? "text-red-400" : res.max_drawdown_pct > 15 ? "text-yellow-400" : "text-green-400"}`}>
                            -{res.max_drawdown_pct}%
                          </p>
                          <p className="text-xs text-gray-400">peak-to-trough</p>
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
                  </div>
                )}
              </div>
            );
          })()}

          {/* Score bucket table */}
          {res.score_buckets && res.score_buckets.length > 0 && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Signal Precision by Confidence Score</p>
              <p className="text-xs text-gray-500 mb-4">
                Among BUY signals in each score range, what % beat the {benchmarkName} benchmark?
                A well-calibrated model shows hit rate rising with score.
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
                      const hr = b.hit_rate_pct ?? 0;
                      const hrColor = hr >= 60 ? "text-green-400" : hr >= 52 ? "text-yellow-400" : "text-red-400";
                      const retColor = (b.avg_return_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400";
                      return (
                        <tr key={b.score_range} className="border-b border-dark-border/50 hover:bg-dark-border/10">
                          <td className="py-2.5 pr-4 font-mono text-white font-semibold">{b.score_range}</td>
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
            </div>
          )}

          {/* Factor IC */}
          {res.factor_ic && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Factor Information Coefficients</p>
              <p className="text-xs text-gray-500 mb-4">
                IC = Pearson correlation between each factor&apos;s score and actual forward return.
                IC &gt; 0.05 = meaningful. IC ≈ 0 = noise. Negative = contrarian.
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
          {stockData?.stocks && stockData.stocks.length > 0 && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-4">
                Per-Stock Results ({stockData.stocks.filter(s => s.buy_signal_count > 0).length} stocks with BUY signals, sorted by BUY return)
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
                    {stockData.stocks
                      .filter(s => s.buy_signal_count > 0)
                      .slice(0, 40)
                      .map((s) => (
                        <tr key={s.symbol} className="border-b border-dark-border/30 hover:bg-dark-border/10">
                          <td className="py-2 pr-3 font-mono font-semibold text-white">{s.symbol}</td>
                          <td className="text-right pr-3 text-gray-400">{s.buy_signal_count}</td>
                          <td className={`text-right pr-3 font-semibold ${s.hit_rate_pct >= 60 ? "text-green-400" : s.hit_rate_pct >= 50 ? "text-yellow-400" : "text-red-400"}`}>
                            {s.hit_rate_pct}%
                          </td>
                          <td className={`text-right font-semibold ${(s.buy_avg_return_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {s.buy_avg_return_pct != null
                              ? `${s.buy_avg_return_pct > 0 ? "+" : ""}${s.buy_avg_return_pct}%`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

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
