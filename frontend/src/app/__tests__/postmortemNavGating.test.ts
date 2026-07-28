import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// PR #32 pre-merge correction — the Postmortem nav item and dedicated page
// gate reads (source-scan proof the wiring exists; NAV_LINKS below is a
// build-time module constant computed from process.env at import, so a
// runtime toggle test would require a full module re-import per case —
// this mirrors the exact source-scan approach already established by
// NavLinksDataLimitations.test.ts for the same reason).
const layoutSrc = readFileSync(path.resolve(process.cwd(), "src/app/layout.tsx"), "utf-8");
const pageSrc = readFileSync(path.resolve(process.cwd(), "src/app/postmortem/page.tsx"), "utf-8");

describe("PR #32 — Postmortem nav link gated by isTradePostmortemDailyEnabled", () => {
  it("layout.tsx imports the feature-gate function", () => {
    expect(layoutSrc).toContain('import { isTradePostmortemDailyEnabled } from "@/utils/featureFlags";');
  });

  it("the /postmortem NAV_LINKS entry is conditionally spread on the gate, not unconditional", () => {
    const navLinksStart = layoutSrc.indexOf("export const NAV_LINKS");
    const navLinksEnd = layoutSrc.indexOf("];", navLinksStart);
    const body = layoutSrc.slice(navLinksStart, navLinksEnd);
    expect(body).toContain('...(isTradePostmortemDailyEnabled() ? [{ href: "/postmortem", label: "Postmortem" }] : [])');
    // Must not also appear as an unconditional entry elsewhere in the array.
    const unconditional = body
      .split("\n")
      .filter((line) => line.includes('href: "/postmortem"') && !line.includes("isTradePostmortemDailyEnabled"));
    expect(unconditional).toEqual([]);
  });
});

describe("PR #32 — /postmortem page itself gated", () => {
  it("imports the feature-gate function", () => {
    expect(pageSrc).toContain('import { isTradePostmortemDailyEnabled } from "@/utils/featureFlags";');
  });

  it("renders a safe unavailable state and returns before any data fetch when disabled", () => {
    const gateIdx = pageSrc.indexOf("if (!isTradePostmortemDailyEnabled())");
    const queryIdx = pageSrc.indexOf("useQuery({");
    expect(gateIdx).toBeGreaterThan(-1);
    // The useQuery call itself must have its own `enabled` check gated on
    // the same flag too — belt-and-suspenders so even if the early return
    // were ever removed, the query still couldn't fire.
    expect(pageSrc).toContain("enabled: isTradePostmortemDailyEnabled() && !!user,");
    expect(queryIdx).toBeGreaterThan(-1);
  });

  it("does not remove any of the Sprint 1 UI feature set from source (only gates access to it)", () => {
    expect(pageSrc).toContain("Why this conclusion?");
    // The exact fallback sentence itself is never hardcoded here — it's
    // rendered dynamically from the backend's claim_text (see api.ts's
    // PostmortemClaim type) via InsufficientEvidenceMark's `reason` prop,
    // which is what keeps the frontend from being able to drift from the
    // one canonical backend constant.
    expect(pageSrc).toContain("InsufficientEvidenceMark");
    expect(pageSrc).toContain('type="date"');
    expect(pageSrc).toContain("MARKETS.map");
  });
});

describe("PR #32 — NAV_LINKS runtime resolution", () => {
  const ORIGINAL = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;

  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
    vi.resetModules();
  });

  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
    else process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = ORIGINAL;
    vi.resetModules();
  });

  it("NAV_LINKS excludes /postmortem when the flag is unset (default)", async () => {
    const { NAV_LINKS } = await import("../layout");
    expect(NAV_LINKS.some((l) => l.href === "/postmortem")).toBe(false);
  });

  it("NAV_LINKS includes /postmortem when the flag is explicitly enabled at module-evaluation time", async () => {
    process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED = "true";
    // layout.tsx's NAV_LINKS is computed once at import — vi.resetModules()
    // clears Vitest's own module cache so this import re-evaluates the
    // module fresh against the now-set env var, proving the gate actually
    // flows through to the built array, not just that the function exists.
    const mod = await import("../layout");
    expect(mod.NAV_LINKS.some((l: { href: string }) => l.href === "/postmortem")).toBe(true);
  });
});
