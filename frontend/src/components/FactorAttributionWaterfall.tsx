"use client";
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

export function FactorAttributionWaterfall({ data }: { data: FactorAttribution }) {
  const sorted = [...data.contributions].sort((a, b) => b.contribution - a.contribution);
  const chartData = sorted.map((c) => ({
    name: c.label,
    value: c.contribution,
  }));

  return (
    <div className="bg-dark-card border border-dark-border rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-white">Factor Attribution</h3>
        <span className="text-xs text-gray-500">
          Composite: <span className="font-mono font-bold text-white">{data.composite_score}</span>
        </span>
      </div>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} layout="vertical" margin={{ left: 8, right: 48, top: 4, bottom: 4 }}>
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
              {chartData.map((entry, i) => (
                <Cell key={i} fill={entry.value >= 0 ? "#22c55e" : "#ef4444"} />
              ))}
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

      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500 border-b border-dark-border">
            <th className="text-left py-1.5 font-medium">Factor</th>
            <th className="text-right py-1.5 font-medium">Contribution</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.factor} className="border-b border-dark-border/50 last:border-0">
              <td className="py-1.5 text-gray-300">{c.label}</td>
              <td
                className={`py-1.5 text-right font-mono font-medium ${
                  c.direction === "positive" ? "text-bull" : "text-bear"
                }`}
              >
                {c.contribution > 0 ? "+" : ""}
                {c.contribution.toFixed(1)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex items-center justify-between text-xs pt-1 border-t border-dark-border">
        <span className="text-bull">Positive: +{data.positive_total.toFixed(1)}</span>
        <span className="text-bear">Negative: {data.negative_total.toFixed(1)}</span>
      </div>
    </div>
  );
}
