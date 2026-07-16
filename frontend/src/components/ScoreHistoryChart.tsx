"use client";
import { useState } from "react";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, LabelList } from "recharts";
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
  // `d` is a date-only string (e.g. "2026-07-15") — new Date(d) parses that
  // as UTC midnight, but toLocaleDateString without an explicit timeZone
  // renders in the BROWSER's local timezone. For any viewer west of UTC
  // (most of the Americas), UTC midnight falls into the previous local
  // evening, so the label could show one day earlier than the actual
  // snapshot date. Pinning timeZone: "UTC" makes the label match exactly
  // what was parsed, regardless of where the viewer is.
  return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

export function ScoreHistoryChart({
  points,
  isLoading,
  isError,
}: {
  points: ScoreHistoryPoint[];
  isLoading?: boolean;
  isError?: boolean;
}) {
  const [view, setView] = useState<"score" | "factors">("score");

  // A real backend failure previously looked identical to "no data yet" —
  // both fell into the same empty-points branch below. A user seeing an
  // error message needs to know something's actually broken (worth
  // retrying/reporting), not that they should just "check back in a few
  // days" for a symbol that in fact has history the fetch failed to load.
  if (isLoading) {
    return (
      <div className="bg-dark-card border border-dark-border rounded-2xl p-6 text-center">
        <h2 className="font-bold text-lg mb-2">Score History</h2>
        <p className="text-gray-500 text-sm animate-pulse">Loading…</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="bg-dark-card border border-dark-border rounded-2xl p-6 text-center">
        <h2 className="font-bold text-lg mb-2">Score History</h2>
        <p className="text-bear text-sm">Couldn&apos;t load score history — try again in a moment.</p>
      </div>
    );
  }

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
            {/* isAnimationActive={false} on every Line below: Recharts' default
                line-draw entrance animation renders the path with
                stroke-dasharray starting at "0 <full-length>" (fully invisible)
                and animates toward fully drawn via requestAnimationFrame. If
                rAF never progresses in a given tab (backgrounded/inactive tab,
                a remount inside a conditionally-rendered panel like the
                History tab here, reduced-motion throttling), the line gets
                stuck at that first, invisible frame — an empty-looking chart
                with valid data and a correctly-drawn-but-hidden path.
                Disabling the animation renders the full path immediately, no
                rAF dependency at all. */}
            {view === "score" ? (
              <Line type="monotone" dataKey="composite_score" name="Composite" stroke="#6366f1" strokeWidth={2}
                dot={{ r: 3, fill: "#6366f1", strokeWidth: 0 }} isAnimationActive={false}>
                <LabelList dataKey="composite_score" position="top" fill="#a5b4fc" fontSize={11} offset={8} />
              </Line>
            ) : (
              // An array, not a <>Fragment</> — Recharts scans LineChart's
              // children by component type (Line/Legend/etc.) to decide what
              // to draw, and does not traverse into a Fragment the same way
              // it flattens an array. Wrapping Legend + the mapped Lines in a
              // Fragment made every one of them invisible to that scan (no
              // error, no legend, no lines — confirmed by direct SVG
              // inspection: the layer never appeared in the DOM at all).
              [
                <Legend key="legend" wrapperStyle={{ fontSize: 11 }} />,
                ...FACTOR_LINES.map((f) => (
                  <Line
                    key={f.key as string}
                    type="monotone"
                    dataKey={f.key as string}
                    name={f.label}
                    stroke={f.color}
                    strokeWidth={1.5}
                    dot={false}
                    connectNulls
                    isAnimationActive={false}
                  />
                )),
              ]
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
