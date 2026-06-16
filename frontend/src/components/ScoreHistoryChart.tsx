"use client";
import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import clsx from "clsx";
import type { ScoreHistoryPoint } from "@/utils/api";

const FACTOR_LINES: { key: keyof ScoreHistoryPoint; label: string; color: string }[] = [
  { key: "technical_score", label: "Technical", color: "#3b82f6" },
  { key: "quality_score", label: "Quality", color: "#22c55e" },
  { key: "valuation_score", label: "Valuation", color: "#f59e0b" },
  { key: "sentiment_score", label: "Sentiment", color: "#a855f7" },
  { key: "risk_score", label: "Risk", color: "#ef4444" },
];

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

export function ScoreHistoryChart({ points }: { points: ScoreHistoryPoint[] }) {
  const [view, setView] = useState<"score" | "factors">("score");

  if (!points || points.length === 0) {
    return (
      <div className="bg-dark-card border border-dark-border rounded-2xl p-6 text-center">
        <h2 className="font-bold text-lg mb-2">Score History</h2>
        <p className="text-gray-500 text-sm">
          No history yet. Daily snapshots accumulate over time — check back in a few days.
        </p>
      </div>
    );
  }

  const data = points.map((p) => ({ ...p, label: fmtDate(p.date) }));

  return (
    <div className="bg-dark-card border border-dark-border rounded-2xl p-6 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-bold text-lg">Score History</h2>
        <div className="flex gap-2">
          {(["score", "factors"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={clsx(
                "px-3 py-1 rounded-lg text-xs font-medium transition-colors",
                view === v ? "bg-brand-500 text-white" : "bg-dark-bg border border-dark-border text-gray-400 hover:text-white"
              )}
            >
              {v === "score" ? "Composite Score" : "Factor Breakdown"}
            </button>
          ))}
        </div>
      </div>

      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ left: -16, right: 8, top: 8, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
            <XAxis dataKey="label" tick={{ fill: "#9ca3af", fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis domain={[0, 100]} tick={{ fill: "#9ca3af", fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip
              contentStyle={{ background: "#1a1d29", border: "1px solid #2a2d3a", borderRadius: 8, fontSize: 12 }}
            />
            {view === "score" ? (
              <Line type="monotone" dataKey="composite_score" name="Composite" stroke="#6366f1" strokeWidth={2} dot={false} />
            ) : (
              <>
                <Legend wrapperStyle={{ fontSize: 11 }} />
                {FACTOR_LINES.map((f) => (
                  <Line
                    key={f.key as string}
                    type="monotone"
                    dataKey={f.key as string}
                    name={f.label}
                    stroke={f.color}
                    strokeWidth={1.5}
                    dot={false}
                    connectNulls
                  />
                ))}
              </>
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
