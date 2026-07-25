import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  resolveActiveJobView,
  type ValidationRunStatus,
} from "@/utils/validationJobView";

// Regression: validation job-identity fix (2026-07).
//
// Original production defect: with "Nifty 100 · Short" selected, the page
// displayed an active run processing US symbols (V, MA, JNJ, PYPL, UNH,
// PFE) because /api/validation/status carried no job identity and the page
// rendered the global run state under whatever tab was selected.

const usShortJob = {
  job_id: "j-1",
  market: "US",
  universe_id: "us",
  horizon: "short",
  status: "running",
  processed: 6,
  total: 200,
};

function running(job: ValidationRunStatus["job"]): ValidationRunStatus {
  return { running: true, progress: 6, total: 200, job };
}

describe("resolveActiveJobView — selected view vs active job", () => {
  it("reproduces the original defect scenario: a US short run under the Nifty 100 short view is FOREIGN, never rendered as this view's progress", () => {
    const view = resolveActiveJobView(running(usShortJob), "nifty100", "short");
    expect(view.showProgressForView).toBe(false);
    expect(view.foreignJob).not.toBeNull();
    expect(view.foreignJob?.universe_id).toBe("us");
    expect(view.foreignJob?.market).toBe("US");
  });

  it("a matching universe but different horizon is still foreign — jobs bind to horizon too", () => {
    const view = resolveActiveJobView(
      running({ ...usShortJob, universe_id: "nifty100", market: "IN" }),
      "nifty100",
      "medium",
    );
    expect(view.showProgressForView).toBe(false);
    expect(view.foreignJob?.horizon).toBe("short");
  });

  it("the matching view renders its own run's progress", () => {
    const view = resolveActiveJobView(running(usShortJob), "us", "short");
    expect(view.showProgressForView).toBe(true);
    expect(view.foreignJob).toBeNull();
  });

  it("switching the selected tab never relabels the job — same status, different selections, identity unchanged", () => {
    const status = running(usShortJob);
    const asNifty = resolveActiveJobView(status, "nifty100", "short");
    const asMidcap = resolveActiveJobView(status, "midcap", "long");
    const asUs = resolveActiveJobView(status, "us", "short");
    expect(asNifty.foreignJob?.job_id).toBe("j-1");
    expect(asMidcap.foreignJob?.job_id).toBe("j-1");
    expect(asUs.showProgressForView).toBe(true);
  });

  it("no active run: nothing to show, nothing foreign", () => {
    const view = resolveActiveJobView(
      { running: false, progress: 0, total: 0, job: null },
      "nifty100",
      "short",
    );
    expect(view.showProgressForView).toBe(false);
    expect(view.foreignJob).toBeNull();
  });

  it("legacy backend without job identity: run stays visible (never hide a real run), not marked foreign", () => {
    const view = resolveActiveJobView(running(null), "nifty100", "short");
    expect(view.showProgressForView).toBe(true);
    expect(view.foreignJob).toBeNull();
  });

  it("undefined status behaves like no run", () => {
    const view = resolveActiveJobView(undefined, "us", "short");
    expect(view.showProgressForView).toBe(false);
    expect(view.foreignJob).toBeNull();
  });
});

describe("validation page wiring — banner and gated log", () => {
  const pageSource = readFileSync(
    path.resolve(process.cwd(), "src/app/validation/page.tsx"),
    "utf-8",
  );

  it("uses the single decision util, not ad hoc inline matching", () => {
    expect(pageSource).toContain(
      'import { resolveActiveJobView } from "@/utils/validationJobView";',
    );
    expect(pageSource).toContain(
      "resolveActiveJobView(status, universe, horizon)",
    );
  });

  it("renders an explicit foreign-job banner naming the running job and the selected view", () => {
    expect(pageSource).toContain('data-testid="foreign-job-banner"');
    expect(pageSource).toContain("validation is currently running");
    expect(pageSource).toContain("You are viewing");
  });

  it("live log/progress is gated on showProgressForView — not on the global running flag alone", () => {
    expect(pageSource).toContain("showProgressForView || (!isRunning");
    expect(pageSource).not.toContain(
      "{(isRunning || (status?.log?.length ?? 0) > 0) && (",
    );
  });
});
