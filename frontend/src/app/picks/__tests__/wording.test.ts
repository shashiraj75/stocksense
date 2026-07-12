import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Learning Alpha Engine remediation, Phase 1 — Investor-facing disclosure.
// picks/page.tsx renders through a data-fetching component that isn't
// practically mountable in isolation here (live market-hours checks, SWR
// fetches, etc.), so this locks in the required user-visible wording as a
// source-text assertion rather than a full render test: it verifies the
// exact strings the audit's remediation phase requires are present, and
// that the retired "AI Confidence" wording is gone. Numeric behavior
// (pick.confidence, scoring) is untouched — only display strings changed.
// Path is relative to the frontend package root (vitest's process.cwd()).
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Daily Picks investor-facing wording (Phase 1 disclosure)", () => {
  it("labels the confidence percentage as Signal Strength", () => {
    expect(pageSource).toContain("Signal Strength");
  });

  it("no longer uses the retired 'AI Confidence' label", () => {
    expect(pageSource).not.toContain("AI Confidence");
  });

  it("discloses that Signal Strength is not a guaranteed probability of profit", () => {
    expect(pageSource).toContain("Signal Strength is not a guaranteed probability of profit.");
  });

  it("labels the model-generated target as Scenario Target", () => {
    expect(pageSource).toContain("Scenario Target");
  });
});
