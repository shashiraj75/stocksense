import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// India Daily Picks session-freshness containment (Phase 0). Same rationale
// as wording.test.ts: page.tsx mounts through live data-fetching (useQuery,
// useRouter, marketHours checks) that isn't practically mountable in
// isolation here, so this locks in the required stale/unknown/fresh
// behavior as source-text assertions. The actual freshness DECISION logic
// (fresh vs. stale vs. unknown, lag-session counting, calendar edge cases)
// is exhaustively unit-tested against fixtures in
// utils/__tests__/sessionFreshness.test.ts — this file only proves page.tsx
// wires that decision into the required UI containment and does not
// recompute any trade value itself.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Daily Picks session-freshness containment — wiring", () => {
  it("is scoped to the IN market only", () => {
    expect(pageSource).toContain('market === "IN"');
    expect(pageSource).toContain("freshnessBySymbol");
  });

  it("computes freshness from evaluateSessionFreshness using only existing fields", () => {
    expect(pageSource).toContain("evaluateSessionFreshness(pick.generation_reference_as_of, now)");
  });

  it("shows the prominent stale warning with the reference date", () => {
    expect(pageSource).toContain("Price reference is stale — this pick used market data from");
    expect(pageSource).toContain("not the latest completed NSE session.");
  });

  it("shows the unknown-freshness warning", () => {
    expect(pageSource).toContain("Price freshness could not be verified.");
  });

  it("replaces 'was ₹X at generation' with a dated, labeled calculation reference for stale/unknown picks", () => {
    expect(pageSource).toContain("Calculation reference:");
    expect(pageSource).toContain('freshness!.freshnessStatus === "stale" ? "stale" : "unverified"');
  });

  it("still uses the original 'was X at generation' wording for the non-stale (fresh/US) path — unchanged behavior", () => {
    expect(pageSource).toContain("was {currency}{pick.price!.toLocaleString(locale)} at generation");
  });

  it("hides estimated upside for stale/unknown picks instead of recomputing it", () => {
    expect(pageSource).toContain("Estimated upside hidden — not actionable until refreshed");
  });

  it("labels trade levels as not actionable and visually de-emphasizes them, without recalculating the values", () => {
    expect(pageSource).toContain("Not actionable until refreshed");
    expect(pageSource).toContain('isStaleOrUnknown && "opacity-50"');
    // The underlying numeric fields themselves are untouched — only their
    // display is replaced with "—" when stale/unknown, never recalculated.
    expect(pageSource).toContain('isStaleOrUnknown ? "—" : pick.entry_low && pick.entry_high');
    expect(pageSource).toContain('isStaleOrUnknown ? "—" : `${currency}${pick.target?.toLocaleString(locale)}`');
    expect(pageSource).toContain('isStaleOrUnknown ? "—" : pick.stop_loss');
  });

  it("disables the Paper Trade button and prefill for stale/unknown picks", () => {
    expect(pageSource).toContain("disabled={isStaleOrUnknown}");
    // Phase A1.1 (Production trade 304 correction, final decision-context
    // pass) — opening the modal now freezes a genuinely copy-based
    // `FrozenDailyPickSnapshot` via `freezeDailyPickSnapshot(pick)` rather
    // than retaining a reference to the live `pick` object, but the
    // containment gate (never opening for a stale/unknown pick) is
    // unchanged.
    expect(pageSource).toContain("if (!isStaleOrUnknown) { setFrozenPick(freezeDailyPickSnapshot(pick)); setShowPaperTrade(true); }");
    expect(pageSource).toContain("showPaperTrade && frozenPick && frozenPick.price && !isStaleOrUnknown");
  });

  it("suppresses the verified-entry-zone-movement banners for stale/unknown picks (no before-and-after framing)", () => {
    expect(pageSource).toContain("verifiedOutside && !isStaleOrUnknown");
    expect(pageSource).toContain("!verifiedOutside && unverifiedQuoteDiffers && !isStaleOrUnknown");
  });

  it("shows a page-level batch notice with fresh/stale/unknown counts", () => {
    expect(pageSource).toContain("Some India Daily Picks use unverified or outdated price references. Review the freshness status on each card before using trade levels.");
    expect(pageSource).toContain("{freshnessCounts.fresh} fresh · {freshnessCounts.stale} stale · {freshnessCounts.unknown} unknown");
  });

  it("never assumes fresh — freshnessBySymbol is null (not a fabricated fresh default) outside IN", () => {
    expect(pageSource).toContain("const freshnessBySymbol: Record<string, FreshnessResult> | null =");
  });
});

// Material gap closure: pick.summary is backend-generated prose that can
// itself contain "Target ₹X implies Y% upside" language computed from the
// same stale/unverified reference as the structured fields above it — it
// must not be the one place that language still leaks through for a
// stale/unknown pick. pick.risk_reward is a derived actionable metric
// (implies knowledge of the entry/stop/target spread) and was found during
// this audit to be rendered unconditionally, ungated by freshness.
describe("Daily Picks session-freshness containment — summary and risk/reward gap closure", () => {
  it("withholds pick.summary for stale/unknown picks behind the isStaleOrUnknown branch", () => {
    expect(pageSource).toContain("{isStaleOrUnknown ? (");
    expect(pageSource).toContain(
      "Analysis summary withheld because the price reference is stale or could not be verified. Refresh the pick before reviewing price targets or trade levels.",
    );
  });

  it("routes to the neutral withheld message BEFORE ever reaching pick.summary — the raw narrative is unreachable when stale/unknown", () => {
    const start = pageSource.indexOf("{isStaleOrUnknown ? (");
    const withheldIdx = pageSource.indexOf("Analysis summary withheld", start);
    const summaryReadIdx = pageSource.indexOf(") : pick.summary && (", start);
    expect(start).toBeGreaterThan(-1);
    expect(withheldIdx).toBeGreaterThan(start);
    // The withheld-copy branch must appear strictly before the branch that
    // ever reads {pick.summary} — i.e. it's an if/else, not two independent
    // conditions that could both render.
    expect(summaryReadIdx).toBeGreaterThan(withheldIdx);
  });

  it("preserves the original summary rendering, wording, and hasValidGenerationBasis gate unchanged for fresh/US picks", () => {
    expect(pageSource).toContain("Generated pick summary — target and upside figures below are based on the price at generation, not the current displayed price.");
    expect(pageSource).toContain("Generated target/upside context unavailable.");
    expect(pageSource).toContain("hasValidGenerationBasis(pick.price, pick.target)");
    expect(pageSource).toContain("{pick.summary}");
  });

  it("gates the Risk:Reward ratio behind freshness — no longer rendered unconditionally", () => {
    expect(pageSource).toContain("{pick.risk_reward && !isStaleOrUnknown &&");
  });

  it("has no title/aria-label anywhere in the file that exposes a price/target/stop-loss value", () => {
    const attrPattern = /(title=\{?["'`]|aria-label=\{?["'`])([^"'`}]*)/g;
    const priceLeakPattern = /\$\{?currency\}?|pick\.target|pick\.entry_low|pick\.entry_high|pick\.stop_loss|pick\.price/;
    let match: RegExpExecArray | null;
    const offenders: string[] = [];
    while ((match = attrPattern.exec(pageSource)) !== null) {
      if (priceLeakPattern.test(match[2])) offenders.push(match[0]);
    }
    expect(offenders).toEqual([]);
  });
});
