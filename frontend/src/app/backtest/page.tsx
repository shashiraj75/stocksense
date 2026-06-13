"use client";
import { useState } from "react";
import { api, Market, Horizon } from "@/utils/api";
import { SignalBadge } from "@/components/SignalBadge";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";
import clsx from "clsx";
import { FlaskConical, CheckCircle, XCircle } from "lucide-react";

interface BacktestResult {
  symbol: string; market: Market; horizon: Horizon;
  total_tests: number; correct_predictions: number; accuracy_pct: number;
  buy_signals_tested: number; sell_signals_tested: number; hold_signals_tested: number;
  avg_return_on_buy_pct: number; avg_return_on_sell_pct: number;
  profitable_buy_calls: number; profitable_sell_calls: number;
  forward_window_days: number;
  results: {
    date: string; entry_price: number; exit_price: number;
    actual_return_pct: number; predicted_signal: string;
    actual_signal: string; correct: boolean;
  }[];
}

export default function BacktestPage() {
  const [symbol, setSymbol] = useState("AAPL");
  const [market, setMarket] = useState<Market>("US");
  const [horizon, setHorizon] = useState<Horizon>("short");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<BacktestResult | null>(null);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true); setError(""); setData(null);
    try {
      const res = await api.get<BacktestResult>(`/api/backtest/${symbol}`, {
        params: { market, horizon },
      });
      setData(res.data);
    } catch (e: any) {
      setError("Failed to run backtest. Try a different symbol.");
    } finally {
      setLoading(false);
    }
  };

  const horizonLabel = { short: "7 trading days", medium: "3 months", long: "12 months" };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <FlaskConical size={24} className="text-brand-500" />
        <div>
          <h1 className="text-2xl font-bold">Backtest</h1>
          <p className="text-gray-400 text-sm">Test prediction accuracy against historical data</p>
        </div>
      </div>

      {/* Controls */}
      <div className="bg-dark-card border border-dark-border rounded-2xl p-6 flex flex-wrap gap-4 items-end">
        <div className="flex-1 min-w-48">
          <label className="text-xs text-gray-400 mb-1.5 block">Symbol</label>
          <input
            className="w-full bg-dark-bg border border-dark-border rounded-xl px-4 py-2.5 text-white font-mono font-bold text-sm outline-none focus:border-brand-500 uppercase"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && run()}
          />
        </div>
        <div>
          <label className="text-xs text-gray-400 mb-1.5 block">Market</label>
          <div className="flex gap-2">
            {(["US", "IN"] as Market[]).map(m => (
              <button key={m} onClick={() => setMarket(m)}
                className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium border transition-colors",
                  market === m ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                {m === "US" ? "🇺🇸" : "🇮🇳"} {m}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-xs text-gray-400 mb-1.5 block">Horizon</label>
          <div className="flex gap-2">
            {(["short", "medium", "long"] as Horizon[]).map(h => (
              <button key={h} onClick={() => setHorizon(h)}
                className={clsx("px-4 py-2.5 rounded-xl text-sm font-medium border capitalize transition-colors",
                  horizon === h ? "bg-brand-500 text-white border-brand-500" : "bg-dark-bg border-dark-border text-gray-400 hover:text-white")}>
                {h}
              </button>
            ))}
          </div>
        </div>
        <button onClick={run} disabled={loading}
          className="px-6 py-2.5 rounded-xl bg-brand-500 text-white font-medium text-sm hover:bg-brand-600 disabled:opacity-50 transition-colors">
          {loading ? "Running…" : "Run Backtest"}
        </button>
      </div>

      {loading && (
        <div className="bg-dark-card border border-dark-border rounded-2xl p-10 text-center">
          <div className="text-gray-400 text-sm animate-pulse">
            Analysing historical data for {symbol}… this takes 20–40 seconds
          </div>
        </div>
      )}

      {error && <p className="text-bear text-sm">{error}</p>}

      {data && (
        <div className="space-y-6">
          {/* Summary cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: "Overall Accuracy", value: `${data.accuracy_pct}%`,
                color: data.accuracy_pct >= 60 ? "text-bull" : data.accuracy_pct >= 45 ? "text-neutral" : "text-bear" },
              { label: "Tests Run", value: data.total_tests, color: "text-white" },
              { label: "Correct Calls", value: data.correct_predictions, color: "text-bull" },
              { label: "Forward Window", value: horizonLabel[data.horizon], color: "text-white" },
            ].map(c => (
              <div key={c.label} className="bg-dark-card border border-dark-border rounded-2xl p-5">
                <p className="text-xs text-gray-400 mb-1">{c.label}</p>
                <p className={clsx("text-2xl font-bold", c.color)}>{c.value}</p>
              </div>
            ))}
          </div>

          {/* Signal breakdown */}
          <div className="grid md:grid-cols-3 gap-4">
            <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <SignalBadge signal="BUY" size="sm" />
                <span className="text-sm text-gray-400">{data.buy_signals_tested} signals</span>
              </div>
              <p className="text-sm text-gray-300">
                Avg return when BUY predicted:
                <span className={clsx("ml-2 font-bold", data.avg_return_on_buy_pct >= 0 ? "text-bull" : "text-bear")}>
                  {data.avg_return_on_buy_pct >= 0 ? "+" : ""}{data.avg_return_on_buy_pct}%
                </span>
              </p>
              <p className="text-xs text-gray-500">{data.profitable_buy_calls} of {data.buy_signals_tested} were profitable</p>
            </div>
            <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <SignalBadge signal="SELL" size="sm" />
                <span className="text-sm text-gray-400">{data.sell_signals_tested} signals</span>
              </div>
              <p className="text-sm text-gray-300">
                Avg return when SELL predicted:
                <span className={clsx("ml-2 font-bold", data.avg_return_on_sell_pct <= 0 ? "text-bull" : "text-bear")}>
                  {data.avg_return_on_sell_pct >= 0 ? "+" : ""}{data.avg_return_on_sell_pct}%
                </span>
              </p>
              <p className="text-xs text-gray-500">{data.profitable_sell_calls} of {data.sell_signals_tested} declined as predicted</p>
            </div>
            <div className="bg-dark-card border border-dark-border rounded-2xl p-5 space-y-3">
              <div className="flex items-center justify-between">
                <SignalBadge signal="HOLD" size="sm" />
                <span className="text-sm text-gray-400">{data.hold_signals_tested} signals</span>
              </div>
              <ConfidenceMeter value={data.accuracy_pct} label="Overall signal accuracy" />
            </div>
          </div>

          {/* Historical results table */}
          <div className="bg-dark-card border border-dark-border rounded-2xl overflow-hidden">
            <div className="px-6 py-4 border-b border-dark-border">
              <h2 className="font-semibold">Recent Test Windows (last 30)</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-dark-border text-gray-400 text-left">
                    <th className="px-4 py-3 font-medium">Date</th>
                    <th className="px-4 py-3 font-medium text-right">Entry</th>
                    <th className="px-4 py-3 font-medium text-right">Exit</th>
                    <th className="px-4 py-3 font-medium text-right">Return</th>
                    <th className="px-4 py-3 font-medium">Predicted</th>
                    <th className="px-4 py-3 font-medium">Actual</th>
                    <th className="px-4 py-3 font-medium text-center">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {[...data.results].reverse().map((r, i) => (
                    <tr key={i} className={clsx("border-b border-dark-border",
                      r.correct ? "bg-bull/5" : "bg-bear/5")}>
                      <td className="px-4 py-3 text-gray-400 font-mono text-xs">{r.date}</td>
                      <td className="px-4 py-3 text-right font-mono">${r.entry_price}</td>
                      <td className="px-4 py-3 text-right font-mono">${r.exit_price}</td>
                      <td className={clsx("px-4 py-3 text-right font-mono font-bold",
                        r.actual_return_pct >= 0 ? "text-bull" : "text-bear")}>
                        {r.actual_return_pct >= 0 ? "+" : ""}{r.actual_return_pct}%
                      </td>
                      <td className="px-4 py-3">
                        <SignalBadge signal={r.predicted_signal as any} size="sm" />
                      </td>
                      <td className="px-4 py-3">
                        <SignalBadge signal={r.actual_signal as any} size="sm" />
                      </td>
                      <td className="px-4 py-3 text-center">
                        {r.correct
                          ? <CheckCircle size={16} className="text-bull inline" />
                          : <XCircle size={16} className="text-bear inline" />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
