import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// User-reported: (1) the "AI Prediction" card showed Confidence twice in a
// row (Signal Strip + a standalone ConfidenceMeter immediately below, using
// a different muting threshold from the strip); (2) Trade Levels rendered
// identically detailed/precise regardless of confidence, even for a
// muted-gray 12%-confidence BUY. page.tsx mounts through live data-fetching
// impractical to isolate here, matching this file's existing test
// convention — source-text assertions.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/stock/[symbol]/page.tsx"),
  "utf-8",
);

describe("AI Prediction card shows Confidence only once", () => {
  it("the standalone ConfidenceMeter duplicating the Signal Strip's confidence is gone", () => {
    expect(pageSource).not.toContain('<ConfidenceMeter\n                    value={prediction.confidence}\n                    label="Confidence"\n                  />');
  });

  it("the Signal Strip's confidence display is still present", () => {
    expect(pageSource).toContain('<p className="text-xs text-gray-400 mb-0.5">Confidence</p>');
    expect(pageSource).toContain('{prediction.confidence}<span className="text-sm font-medium">%</span></p>');
  });

  it("ConfidenceMeter is still used elsewhere on the page (import must remain valid)", () => {
    const count = (pageSource.match(/<ConfidenceMeter/g) ?? []).length;
    expect(count).toBeGreaterThan(0);
  });
});

describe("Trade Levels discloses low confidence", () => {
  it("shows a warning when confidence is below 45 — the same threshold getSignalTone already uses to mute a weak BUY", () => {
    expect(pageSource).toContain("{prediction.confidence < 45 && (");
    expect(pageSource).toContain("Low confidence ({prediction.confidence}%)");
    expect(pageSource).toContain("not a high-conviction trade setup");
  });

  it("the warning renders before the trade-level boxes, not buried after them", () => {
    const warnIdx = pageSource.indexOf("Low confidence ({prediction.confidence}%)");
    const gridIdx = pageSource.indexOf("className={`grid ${gridCols} gap-4`}");
    expect(warnIdx).toBeGreaterThan(-1);
    expect(gridIdx).toBeGreaterThan(-1);
    expect(warnIdx).toBeLessThan(gridIdx);
  });
});
