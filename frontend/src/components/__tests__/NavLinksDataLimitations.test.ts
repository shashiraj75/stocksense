import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// DP-026 — the nav-bar AccuracyBadge (visible to every logged-in user, all
// markets/horizons since it's a single global badge) must carry the
// compact, non-tooltip-only marker.
const src = readFileSync(path.resolve(process.cwd(), "src/components/NavLinks.tsx"), "utf-8");

describe("DP-026 disclosure wiring — NavLinks AccuracyBadge", () => {
  it("imports DataLimitationsMark", () => {
    expect(src).toContain('import { DataLimitationsMark } from "@/components/DataLimitationsNotice";');
  });

  it("renders the mark inside AccuracyBadge, not conditioned on a title-only affordance", () => {
    const fnStart = src.indexOf("function AccuracyBadge");
    const fnEnd = src.indexOf("\nexport function NavLinks", fnStart);
    const body = src.slice(fnStart, fnEnd);
    expect(body).toContain("<DataLimitationsMark />");
  });

  it("does not gate the marker behind the same early-return that hides the badge for low sample sizes — it only needs to appear whenever the badge itself does, which the shared return statement already guarantees", () => {
    const fnStart = src.indexOf("function AccuracyBadge");
    const fnEnd = src.indexOf("\nexport function NavLinks", fnStart);
    const body = src.slice(fnStart, fnEnd);
    expect(body).toContain("if (!accuracy || n < 10) return null;");
    // Mark must be after the early return in source order (same render path).
    expect(body.indexOf("<DataLimitationsMark />")).toBeGreaterThan(body.indexOf("if (!accuracy"));
  });
});
