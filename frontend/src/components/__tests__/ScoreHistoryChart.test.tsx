import React from "react";
import fs from "fs";
import path from "path";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScoreHistoryChart } from "../ScoreHistoryChart";
import type { ScoreHistoryPoint } from "@/utils/api";

// Recharts' ResponsiveContainer measures its parent via ResizeObserver,
// which jsdom doesn't implement — without this, LineChart never receives a
// nonzero width/height and renders nothing. Cloning children with a fixed
// size is the standard workaround for testing Recharts under jsdom and only
// affects the test environment, not production.
//
// Deliberately NOT wrapping Line/LabelList themselves here: Recharts'
// internal axis/graphical-item code imports Line from its own internal path
// (not the top-level "recharts" export), so replacing the exported `Line`
// reference breaks its own identity checks (confirmed: produces
// "Cannot read properties of undefined (reading 'yAxisId')", then silently
// renders zero `.recharts-line` elements even once that crash is patched
// around) — real rendering requires the real, un-wrapped Line.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement }) =>
      React.cloneElement(children, { width: 600, height: 300 }),
  };
});

function points(): ScoreHistoryPoint[] {
  return [
    {
      date: "2026-07-01", composite_score: 70, technical_score: 60, quality_score: 65,
      valuation_score: 55, sentiment_score: 50, risk_score: 80,
    } as ScoreHistoryPoint,
    {
      date: "2026-07-02", composite_score: 75, technical_score: 62, quality_score: 66,
      valuation_score: 58, sentiment_score: null as unknown as number, risk_score: 82,
    } as ScoreHistoryPoint,
  ];
}

describe("ScoreHistoryChart", () => {
  it("renders 'No history yet' for an empty points array, not a broken chart", () => {
    render(<ScoreHistoryChart points={[]} />);
    expect(screen.getByText(/No history yet/i)).toBeInTheDocument();
  });

  it("Composite Score view (default) renders the composite score values as visible labels, not just on hover", () => {
    const { container } = render(<ScoreHistoryChart points={points()} />);
    expect(container.querySelectorAll(".recharts-line").length).toBeGreaterThan(0);
    // Recharts' LabelList text is rendered with a distinct class from the
    // Y-axis tick text — both can legitimately show "75" (a datapoint value
    // AND a coincidental axis tick), so scope the query to LabelList only.
    const labelTexts = [...container.querySelectorAll(".recharts-label")].map((el) => el.textContent);
    expect(labelTexts).toContain("70");
    expect(labelTexts).toContain("75");
  });

  it("Factor Breakdown view renders the legend and every factor line, without relying on Fragment children", () => {
    const { container } = render(<ScoreHistoryChart points={points()} />);
    fireEvent.click(screen.getByRole("button", { name: "Factor Breakdown" }));

    // Legend entries — one per FACTOR_LINES entry. This is the exact
    // regression this test guards: a <>Fragment</> wrapping Legend + the
    // mapped Lines was invisible to Recharts' children-type scan (no error,
    // no legend, no lines — confirmed previously by this same "0 elements"
    // symptom this test would catch).
    expect(screen.getByText("Technical")).toBeInTheDocument();
    expect(screen.getByText("Quality")).toBeInTheDocument();
    expect(screen.getByText("Valuation")).toBeInTheDocument();
    expect(screen.getByText("Sentiment")).toBeInTheDocument();
    expect(screen.getByText("Risk")).toBeInTheDocument();
    // 5 factor lines actually drawn in the DOM, not silently swallowed.
    expect(container.querySelectorAll(".recharts-line").length).toBe(5);
  });

  it("handles a null factor value (e.g. missing sentiment_score) without crashing", () => {
    expect(() => {
      render(<ScoreHistoryChart points={points()} />);
      fireEvent.click(screen.getByRole("button", { name: "Factor Breakdown" }));
    }).not.toThrow();
  });

  it("switching back to Composite Score from Factor Breakdown still shows the composite labels", () => {
    render(<ScoreHistoryChart points={points()} />);
    fireEvent.click(screen.getByRole("button", { name: "Factor Breakdown" }));
    fireEvent.click(screen.getByRole("button", { name: "Composite Score" }));
    expect(screen.getByText("70")).toBeInTheDocument();
  });
});

// Source-text checks (SES-003 §2's "code shape" style: valid, and the right
// tool, when the property under test is about code shape rather than
// runtime behavior) — Recharts' own internal identity checks make it
// impractical to spy on Line's props without breaking its rendering (see
// the vi.mock comment above), so isAnimationActive/connectNulls are
// asserted directly against the source rather than indirectly inferred
// from a DOM attribute Recharts doesn't expose in a stable, test-friendly way.
describe("ScoreHistoryChart source shape", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "ScoreHistoryChart.tsx"),
    "utf-8",
  );

  it("every <Line> has isAnimationActive={false} (prevents the stuck-invisible-line regression)", () => {
    // Source has exactly 2 distinct <Line> JSX literals: the Composite Score
    // line, and the single template FACTOR_LINES.map() reuses for all 5
    // factor lines at runtime (confirmed by the DOM-level test above finding
    // 5 rendered .recharts-line elements from this one template).
    const lineOpenTags: string[] = source.match(/<Line\b[\s\S]*?(?:\/>|>)/g) ?? [];
    expect(lineOpenTags.length).toBe(2);
    for (const tag of lineOpenTags) {
      expect(tag).toContain("isAnimationActive={false}");
    }
  });

  it("every factor <Line> has connectNulls (a null datapoint must not break the line)", () => {
    const lineOpenTags: string[] = source.match(/<Line\b[\s\S]*?(?:\/>|>)/g) ?? [];
    const factorLineTags = lineOpenTags.filter((tag) => tag.includes('dataKey={f.key'));
    expect(factorLineTags.length).toBeGreaterThan(0);
    for (const tag of factorLineTags) {
      expect(tag).toContain("connectNulls");
    }
  });

  it("the Factor Breakdown branch returns an array, not a Fragment (the exact regression this whole test guards)", () => {
    // The historical bug: `<>...</>` around Legend + mapped Lines was
    // invisible to Recharts' children-type scan. Guard against it coming
    // back by asserting the branch is a bracketed array literal, not a
    // Fragment shorthand, in the source itself.
    expect(source).not.toMatch(/<>\s*<Legend/);
    expect(source).toMatch(/\[\s*<Legend/);
  });
});
