"use client";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/utils/api";
import {
  FlaskConical, TrendingUp, TrendingDown, Target, Zap,
  BarChart3, CheckCircle2, XCircle, AlertCircle, Loader2, Play,
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
  avg_return_on_sell_pct?: number | null;
  avg_return_benchmark_pct?: number | null;
  buy_outperformance_pct?: number | null;
  sharpe_on_buys?: number | null;
  profitable_buy_pct?: number | null;
  score_buckets?: ScoreBucket[];
  factor_ic?: FactorIC;
  nifty_avg_fwd_return_pct?: number | null;
};

type StockResult = {
  symbol: string;
  total_signals: number;
  hit_rate_pct: number;
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
  { key: "short",  label: "Short (5 days)",   sub: "1–5 day forward return" },
  { key: "medium", label: "Medium (21 days)",  sub: "~1 month forward return" },
  { key: "long",   label: "Long (63 days)",    sub: "~3 month forward return" },
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
  const pct = Math.round(Math.min(100, Math.abs(value) * 500)); // scale: IC=0.20 → 100%
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
          ? "🟡 Weak but positive"
          : "❌ Near zero — adds no predictive value"}
      </p>
    </div>
  );
}

export default function ValidationPage() {
  const [horizon, setHorizon] = useState<"short" | "medium" | "long">("medium");
  const [nStocks, setNStocks] = useState(50);
  const qc = useQueryClient();

  const { data: results, isLoading: resultsLoading } = useQuery<ValidationResult>({
    queryKey: ["validation-results", horizon],
    queryFn: () => api.get(`/api/validation/results?horizon=${horizon}`).then(r => r.data),
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });

  const { data: stockData } = useQuery<{ stocks: StockResult[] }>({
    queryKey: ["validation-stocks", horizon],
    queryFn: () => api.get(`/api/validation/results/stocks?horizon=${horizon}`).then(r => r.data),
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
    mutationFn: () => api.post(`/api/validation/run?horizon=${horizon}&n_stocks=${nStocks}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["validation-status"] });
      qc.invalidateQueries({ queryKey: ["validation-results"] });
    },
  });

  const res = results?.available ? results : null;
  const isRunning = status?.running === true;

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
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
            Does the AI model actually predict stock returns? This page answers that with historical evidence.
          </p>
        </div>
      </div>

      {/* Warning banner */}
      <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3">
        <AlertCircle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
        <div className="text-sm text-yellow-300/80">
          <strong className="text-yellow-300">Walk-forward guarantee:</strong> At each historical date, the model only uses data available <em>before</em> that date — no look-ahead bias.
          This measures the model's <em>real</em> predictive power, not overfitting.
          Validation uses the full technical scoring engine (RSI, MACD, EMA, ADX, Bollinger, OBV, MFI, Relative Strength) across Nifty 100 stocks.
        </div>
      </div>

      {/* Horizon selector + run controls */}
      <div className="bg-dark-card border border-dark-border rounded-xl p-4 space-y-4">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Configure & Run</p>
        <div className="flex flex-wrap gap-2">
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

        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400">Stocks to test:</label>
            {[25, 50, 100].map(n => (
              <button
                key={n}
                onClick={() => setNStocks(n)}
                className={`px-3 py-1 rounded text-xs font-medium ${nStocks === n ? "bg-purple-600 text-white" : "bg-dark-border text-gray-400"}`}
              >
                {n}
              </button>
            ))}
          </div>
          <button
            onClick={() => triggerRun()}
            disabled={isRunning || triggering}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-lg text-sm font-semibold transition-colors"
          >
            {isRunning ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {isRunning ? `Running… ${status?.progress}/${status?.total}` : "Run Validation"}
          </button>
        </div>

        {/* Live log */}
        {(isRunning || (status?.log?.length ?? 0) > 0) && (
          <div className="bg-black/40 rounded-lg p-3 max-h-32 overflow-y-auto font-mono text-xs text-gray-400 space-y-0.5">
            {(status?.log ?? []).slice(-15).map((line, i) => (
              <div key={i} className={line.startsWith("✅") ? "text-green-400" : line.startsWith("❌") ? "text-red-400" : ""}>{line}</div>
            ))}
            {isRunning && <div className="text-purple-400 animate-pulse">▋</div>}
          </div>
        )}
      </div>

      {/* No results yet */}
      {!res && !resultsLoading && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <BarChart3 size={40} className="text-gray-600 mb-4" />
          <h3 className="text-lg font-semibold text-gray-300 mb-2">No validation results yet</h3>
          <p className="text-sm text-gray-500 max-w-sm">
            Click "Run Validation" above to start a walk-forward backtest.
            The {horizon}-term run typically takes 3–8 minutes for 50 stocks.
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
            <span>Horizon: <strong className="text-gray-300">{res.horizon}</strong></span>
            <span>Stocks tested: <strong className="text-gray-300">{res.n_stocks_tested}</strong></span>
            <span>Total signals: <strong className="text-gray-300">{res.total_signals?.toLocaleString()}</strong></span>
            <span>Run at: <strong className="text-gray-300">
              {res.run_at ? new Date(res.run_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—"}
            </strong></span>
          </div>

          {/* Key metrics grid */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="BUY Hit Rate"
              value={res.buy_hit_rate_pct != null ? `${res.buy_hit_rate_pct}%` : null}
              sub="% of BUY calls that rose by threshold"
              color={
                (res.buy_hit_rate_pct ?? 0) >= 60 ? "text-green-400" :
                (res.buy_hit_rate_pct ?? 0) >= 50 ? "text-yellow-400" : "text-red-400"
              }
              icon={<Target size={14} />}
            />
            <StatCard
              label="Avg Return on BUY"
              value={res.avg_return_on_buy_pct != null ? `${res.avg_return_on_buy_pct > 0 ? "+" : ""}${res.avg_return_on_buy_pct}%` : null}
              sub={`vs Nifty ${res.nifty_avg_fwd_return_pct != null ? res.nifty_avg_fwd_return_pct + "%" : "—"} baseline`}
              color={(res.avg_return_on_buy_pct ?? 0) > 0 ? "text-green-400" : "text-red-400"}
              icon={<TrendingUp size={14} />}
            />
            <StatCard
              label="Outperformance vs Nifty"
              value={res.buy_outperformance_pct != null ? `${res.buy_outperformance_pct > 0 ? "+" : ""}${res.buy_outperformance_pct}%` : null}
              sub="BUY call avg return minus benchmark"
              color={(res.buy_outperformance_pct ?? 0) > 0 ? "text-green-400" : "text-red-400"}
              icon={<Zap size={14} />}
            />
            <StatCard
              label="Sharpe (BUY calls)"
              value={res.sharpe_on_buys != null ? res.sharpe_on_buys.toFixed(2) : null}
              sub=">1.0 = good risk-adjusted return"
              color={
                (res.sharpe_on_buys ?? 0) >= 1.0 ? "text-green-400" :
                (res.sharpe_on_buys ?? 0) >= 0.5 ? "text-yellow-400" : "text-red-400"
              }
              icon={<BarChart3 size={14} />}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              label="Profitable BUY %"
              value={res.profitable_buy_pct != null ? `${res.profitable_buy_pct}%` : null}
              sub="% of BUY calls with any positive return"
              color={(res.profitable_buy_pct ?? 0) >= 55 ? "text-green-400" : "text-yellow-400"}
            />
            <StatCard
              label="SELL Hit Rate"
              value={res.sell_hit_rate_pct != null ? `${res.sell_hit_rate_pct}%` : null}
              sub="% of SELL calls that fell"
              color={(res.sell_hit_rate_pct ?? 0) >= 55 ? "text-green-400" : "text-yellow-400"}
              icon={<TrendingDown size={14} />}
            />
            <StatCard
              label="Overall Accuracy"
              value={res.overall_accuracy_pct != null ? `${res.overall_accuracy_pct}%` : null}
              sub="All signals (BUY + SELL + HOLD)"
            />
            <StatCard
              label="BUY Signals Tested"
              value={res.buy_signals?.toLocaleString() ?? null}
              sub={`of ${res.total_signals?.toLocaleString()} total signals`}
            />
          </div>

          {/* Score bucket table — the investor's guide */}
          {res.score_buckets && res.score_buckets.length > 0 && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Signal Precision by Confidence Score</p>
              <p className="text-xs text-gray-500 mb-4">
                When the AI score is in each range, how often did the stock actually go up? Higher score → higher hit rate = well-calibrated model.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-xs text-gray-500 border-b border-dark-border">
                      <th className="text-left py-2 pr-4">AI Score Range</th>
                      <th className="text-right py-2 pr-4">Signals</th>
                      <th className="text-right py-2 pr-4">Hit Rate</th>
                      <th className="text-right py-2">Avg Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.score_buckets.map((b) => {
                      const hr = b.hit_rate_pct ?? 0;
                      const hrColor = hr >= 65 ? "text-green-400" : hr >= 55 ? "text-yellow-400" : "text-red-400";
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
              <p className="text-xs text-gray-600 mt-3">
                Interpretation: A well-calibrated model should show monotonically increasing hit rate as the score rises.
                If the 80–85 bucket has a higher hit rate than 85–90, the score is over-confident at the top.
              </p>
            </div>
          )}

          {/* Factor IC */}
          {res.factor_ic && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-1">Factor Information Coefficients</p>
              <p className="text-xs text-gray-500 mb-4">
                IC = Pearson correlation between each factor's score and actual forward return.
                IC &gt; 0.05 = meaningful signal. IC ≈ 0 = noise. Negative IC = contrarian indicator.
              </p>
              <div className="space-y-4">
                <ICBar label="Composite Score"   value={res.factor_ic.composite} color="bg-purple-500" />
                <ICBar label="Technical (RSI/MACD/EMA/ADX/BB)" value={res.factor_ic.tech} color="bg-blue-500" />
                <ICBar label="Relative Strength vs Nifty"       value={res.factor_ic.rs}   color="bg-green-500" />
                <ICBar label="OBV Trend (Volume Flow)"          value={res.factor_ic.obv}  color="bg-yellow-500" />
                <ICBar label="MFI (Money Flow Index)"           value={res.factor_ic.mfi}  color="bg-orange-500" />
              </div>
              <div className="mt-4 p-3 bg-dark-border/30 rounded-lg text-xs text-gray-400 space-y-1">
                <p>📐 <strong className="text-gray-300">IC &gt; 0.05</strong> — statistically meaningful; this factor genuinely predicts returns</p>
                <p>📐 <strong className="text-gray-300">IC 0.02–0.05</strong> — weak but real alpha; worth including at reduced weight</p>
                <p>📐 <strong className="text-gray-300">IC &lt; 0.02</strong> — effectively noise; consider removing or downweighting this factor</p>
              </div>
            </div>
          )}

          {/* Per-stock table */}
          {stockData?.stocks && stockData.stocks.length > 0 && (
            <div className="bg-dark-card border border-dark-border rounded-xl p-5">
              <p className="text-sm font-semibold text-white mb-4">Per-Stock Results (sorted by BUY return)</p>
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
                    {stockData.stocks.slice(0, 30).map((s) => (
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
                  <p><strong className="text-gray-300">BUY hit rate &gt; 60%</strong> — model reliably picks upward moves. Ready to use as a stock screener.</p>
                </div>
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Avg BUY return &gt; benchmark</strong> — model adds alpha vs. just holding Nifty 50.</p>
                </div>
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Sharpe &gt; 1.0</strong> — model's returns are worth the risk.</p>
                </div>
                <div className="flex items-start gap-2">
                  <CheckCircle2 size={14} className="text-green-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Score buckets monotonically increasing</strong> — higher AI score reliably = higher hit rate = model is calibrated.</p>
                </div>
              </div>
              <div className="space-y-2">
                <div className="flex items-start gap-2">
                  <XCircle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">BUY hit rate &lt; 52%</strong> — barely better than a coin flip. Don't use for investment decisions.</p>
                </div>
                <div className="flex items-start gap-2">
                  <XCircle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Avg BUY return &lt; benchmark</strong> — model underperforms buy-and-hold Nifty. Not worth using.</p>
                </div>
                <div className="flex items-start gap-2">
                  <XCircle size={14} className="text-red-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Factor IC near zero</strong> — that factor adds noise, not signal. Remove it from the model.</p>
                </div>
                <div className="flex items-start gap-2">
                  <AlertCircle size={14} className="text-yellow-400 mt-0.5 shrink-0" />
                  <p><strong className="text-gray-300">Backtest is not a guarantee</strong> — past model performance doesn't guarantee future returns. Use alongside your own research.</p>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
