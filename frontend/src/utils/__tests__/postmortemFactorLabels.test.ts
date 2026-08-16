import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { getFactorLabel, humanizeFactor } from "@/utils/postmortemFactorLabels";

// Genuine introspection against the same evidence-coverage matrix the
// backend's registry-introspection test validates — every (report_section,
// internal_factor) pair the current governed rule inventory can emit MUST
// resolve to a curated label. This is not a hand-typed duplicate list: it
// reads the same JSON file the backend test reads, so a new governed
// factor added to the matrix without a matching frontend label fails here.
const __dirname = dirname(fileURLToPath(import.meta.url));
const MATRIX_PATH = resolve(
  __dirname, "../../../../backend/tests/unit/postmortem_evidence_coverage_matrix.json"
);

interface MatrixEntry {
  report_section: string;
  internal_factor: string;
}

function loadMatrixEntries(): MatrixEntry[] {
  const raw = JSON.parse(readFileSync(MATRIX_PATH, "utf-8"));
  return raw.entries;
}

describe("postmortemFactorLabels — zero uncurated labels for current governed inventory", () => {
  it("every (report_section, internal_factor) pair in the evidence-coverage matrix has a curated label", () => {
    const entries = loadMatrixEntries();
    expect(entries.length).toBeGreaterThan(0);

    const uncurated: string[] = [];
    for (const e of entries) {
      const result = getFactorLabel(e.report_section, e.internal_factor);
      if (!result.curated) {
        uncurated.push(`${e.report_section}::${e.internal_factor}`);
      }
    }
    expect(uncurated).toEqual([]);
  });

  it("an unknown future factor NOT in the matrix still renders safely humanized, not curated", () => {
    const result = getFactorLabel("some_future_section", "SOME_BRAND_NEW_FACTOR");
    expect(result.curated).toBe(false);
    expect(result.title).toBe(humanizeFactor("SOME_BRAND_NEW_FACTOR"));
    expect(result.title.length).toBeGreaterThan(0);
  });
});
