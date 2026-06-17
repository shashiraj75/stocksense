"use client";
import { useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Cell, ResponsiveContainer, Tooltip, LabelList } from "recharts";

export interface FactorContribution {
  factor: string;
  label: string;
  contribution: number;
  direction: "positive" | "negative";
}

export interface FactorAttribution {
  symbol: string;
  horizon: string;
  composite_score: number;
  contributions: FactorContribution[];
  positive_total: number;
  negative_total: number;
  generated_at: string;
}

// Map factor keys to detail extractors from the prediction object
function getFactorDetails(factor: string, prediction: any): string[] {
  if (!prediction) return [];

  const sd = prediction.fundamental_score?.reasons ?? [];
  const tech = prediction.technical ?? {};
  const qf = prediction.quality_factors ?? {};
  const gc = prediction.global_context ?? {};
  const regime = prediction.market_regime ?? {};
  const sent = prediction.sentiment_score ?? {};

  switch (factor) {
    case "fundamental": {
      const reasons: string[] = sd.slice(0, 6);
      const screener = prediction._screener_data ?? {};
      if (prediction.fundamental_score?.score != null)
        reasons.unshift(`Score: ${prediction.fundamental_score.score}/100`);
      return reasons;
    }
    case "technical": {
      const lines: string[] = [];
      if (tech.trend)      lines.push(`Trend: ${tech.trend}`);
      if (tech.rsi != null) lines.push(`RSI: ${tech.rsi.toFixed(1)}`);
      if (tech.macd_signal) lines.push(`MACD: ${tech.macd_signal}`);
      if (tech.bb_position) lines.push(`Bollinger: ${tech.bb_position}`);
      if (tech.volume_signal) lines.push(`Volume: ${tech.volume_signal}`);
      if (tech.pattern)    lines.push(`Pattern: ${tech.pattern}`);
      if (tech.score != null) lines.unshift(`Score: ${tech.score}/100`);
      return lines;
    }
    case "sentiment": {
      const lines: string[] = [];
      if (sent.score != null)   lines.push(`Score: ${sent.score}/100`);
      if (sent.label)           lines.push(`Sentiment: ${sent.label}`);
      if (sent.article_count != null) lines.push(`Articles analysed: ${sent.article_count}`);
      if (sent.bullish_count != null) lines.push(`Bullish: ${sent.bullish_count}  Bearish: ${sent.bearish_count ?? 0}`);
      return lines;
    }
    case "quality": {
      const lines: string[] = [];
      if (qf.score != null)     lines.push(`Score: ${qf.score}/100`);
      if (qf.sector)            lines.push(`Sector: ${qf.sector}`);
      if (qf.piotroski != null) lines.push(`Piotroski F-score: ${qf.piotroski}/9`);
      const bd = qf.breakdown ?? {};
      for (const [k, v] of Object.entries(bd)) {
        if (v != null) lines.push(`${k}: ${(v as number * 100).toFixed(0)}%`);
      }
      return lines;
    }
    case "global_macro": {
      const lines: string[] = [];
      if (gc.score != null) lines.push(`Score: ${gc.score}/100`);
      const levels = gc.levels ?? {};
      const changes = gc.changes ?? {};
      for (const [k, v] of Object.entries(levels)) {
        const chg = (changes as any)[k];
        lines.push(`${k}: ${(v as number).toFixed(2)}${chg != null ? ` (${chg > 0 ? "+" : ""}${(chg as number).toFixed(2)}%)` : ""}`);
      }
      return lines;
    }
    case "regime": {
      const lines: string[] = [];
      if (regime.label)     lines.push(`Regime: ${regime.label}`);
      if (regime.trend)     lines.push(`Trend: ${regime.trend}`);
      if (regime.score_adj != null) lines.push(`Adjustment: ${regime.score_adj > 0 ? "+" : ""}${regime.score_adj.toFixed(1)} pts`);
      return lines;
    }
    default:
      return [];
  }
}

export function FactorAttributionWaterfall({
  data,
  prediction,
}: {
  data: FactorAttribution;
  prediction?: any;
}) {
  const [selected, setSelected] = useState<string | null>(null);

  const sorted = [...data.contributions]
    .filter((c) => c.contribution !== 0)
    .sort((a, b) => b.contribution - a.contribution);
  const chartData = sorted.map((c) => ({
    name: c.label,
    factor: c.factor,
    value: c.contribution,
  }));
  const chartHeight = Math.max(160, sorted.length * 32);

  const selectedFactor = sorted.find((c) => c.factor === selected);
  const details = selected ? getFactorDetails(selected, prediction) : [];

  const handleClick = (data: any) => {
    const factor = chartData.find((c) => c.name === data?.activeLabel)?.factor;
    if (!factor) return;
    setSelected((prev) => (prev === factor ? null : factor));
  };

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-white">Factor Attribution</h3>
        <span className="text-xs text-gray-500">
          Composite: <span className="font-mono font-bold text-white">{data.composite_score}</span>
        </span>
      </div>

      <div style={{ height: chartHeight }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            layout="vertical"
            margin={{ left: 8, right: 48, top: 4, bottom: 4 }}
            onClick={handleClick}
            style={{ cursor: "pointer" }}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={110}
              tick={{ fill: "#9ca3af", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{ background: "#1a1d29", border: "1px solid #2a2d3a", borderRadius: 8, fontSize: 12 }}
              formatter={(value: number) => [value.toFixed(1), "Points"]}
            />
            <Bar dataKey="value" radius={[3, 3, 3, 3]}>
              {chartData.map((entry, i) => {
                const isSelected = entry.factor === selected;
                const baseColor = entry.value >= 0 ? "#22c55e" : "#ef4444";
                return (
                  <Cell
                    key={i}
                    fill={isSelected ? (entry.value >= 0 ? "#16a34a" : "#dc2626") : baseColor}
                    opacity={selected && !isSelected ? 0.4 : 1}
                  />
                );
              })}
              <LabelList
                dataKey="value"
                position="right"
                style={{ fill: "#e5e7eb", fontSize: 11, fontFamily: "monospace", fontWeight: 600 }}
                formatter={(v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Detail panel — shown when a bar is clicked */}
      {selected && details.length > 0 && (
        <div className="rounded-lg border border-dark-border bg-dark-bg p-3 space-y-1.5 text-xs">
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold text-white">{selectedFactor?.label}</span>
            <button
              onClick={() => setSelected(null)}
              className="text-gray-500 hover:text-white text-xs"
            >
              ✕
            </button>
          </div>
          {details.map((line, i) => (
            <p key={i} className="text-gray-300 leading-relaxed">{line}</p>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between text-xs pt-1 border-t border-dark-border">
        <span className="text-bull">Positive: +{data.positive_total.toFixed(1)}</span>
        <span className="text-bear">Negative: {data.negative_total.toFixed(1)}</span>
        {!selected && <span className="text-gray-600 italic">Click a bar for details</span>}
      </div>
    </div>
  );
}
