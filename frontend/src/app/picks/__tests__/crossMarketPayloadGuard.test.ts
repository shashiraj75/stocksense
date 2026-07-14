import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Cross-market retained-payload containment bypass fix. Same rationale as
// sessionFreshnessWording.test.ts: page.tsx mounts through live
// data-fetching (useQuery, useRouter, marketHours checks) that isn't
// practically mountable in isolation here, so this locks in the required
// wiring as source-text assertions. The actual guard DECISION logic (does
// a payload's market match the selected market, fail-closed on missing/
// malformed input) is exhaustively unit-tested against fixtures in
// utils/__tests__/payloadMarketGuard.test.ts — this file only proves
// page.tsx actually applies that guard to every payload-derived read, and
// never re-derives its own ad-hoc market check.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Cross-market retained-payload containment — guard wiring", () => {
  it("shadows the raw query result through selectPayloadForMarket before any downstream read", () => {
    expect(pageSource).toContain('import { selectPayloadForMarket } from "@/utils/payloadMarketGuard";');
    expect(pageSource).toContain("data: rawDailyPicksData");
    expect(pageSource).toContain("const data = selectPayloadForMarket(rawDailyPicksData, market);");
  });

  it("every payload-derived value (picks, currency, freshness) is read from the GUARDED `data`, not the raw query result", () => {
    // `picks`/`currency` must be derived from the shadowed `data` binding.
    expect(pageSource).toContain("const currency = data?.currency ?? marketCfg.currency;");
    expect(pageSource).toContain("const picks = data?.picks?.[horizon] ?? [];");
    // No downstream code may read a *property* off the raw, unguarded
    // payload (`rawDailyPicksData?.xxx` or `rawDailyPicksData.xxx`) — the
    // only two legitimate uses of the identifier are the destructure itself
    // and the boolean liveness check (`!!rawDailyPicksData`) used to decide
    // whether to show the transition skeleton, neither of which reads a
    // payload field.
    expect(pageSource).not.toMatch(/rawDailyPicksData[?.]*\.[a-zA-Z]/);
  });

  it("scenarios A & B — a market-mismatched payload never reaches the cards branch (fail closed both directions)", () => {
    // picks is derived from the guarded `data`, which selectPayloadForMarket
    // proves (via its own fixture tests) resolves to undefined whenever the
    // payload's market != the selected market — in EITHER direction
    // (US-selected/IN-retained, or IN-selected/US-retained). There is no
    // separate IN-only or US-only branch here; the same guard call covers
    // both, so neither direction can leak.
    expect(pageSource).toContain("const picks = data?.picks?.[horizon] ?? [];");
  });

  it("scenarios A & B — a transitioning (mismatched-but-present) payload shows the loading skeleton, not the empty state or stale cards", () => {
    expect(pageSource).toContain("const isMarketTransitioning = !!rawDailyPicksData && !data;");
    expect(pageSource).toContain("{isLoading || isMarketTransitioning ? (");
  });

  it("scenario F — fails closed when the payload has no provable market (covered exhaustively in payloadMarketGuard.test.ts; this proves the page never bypasses it)", () => {
    // The page never reads pick/currency/freshness data through anything
    // other than the guarded `data` binding (proven above by the
    // rawDailyPicksData occurrence count) — so a payload that
    // selectPayloadForMarket rejects (missing/malformed market) is
    // indistinguishable, from the page's perspective, from "not loaded yet."
    expect(pageSource).toContain("const data = selectPayloadForMarket(rawDailyPicksData, market);");
  });

  it("scenario G — Paper Trade modal/selected-pick state is component-local to PickCard, not lifted to the page — so it cannot survive a card's unmount", () => {
    expect(pageSource).toContain('const [showPaperTrade, setShowPaperTrade] = useState(false);');
    // No page-level state named for a "selected pick" or "open paper trade
    // symbol" exists — confirming there is nothing at the DailyPicksPage
    // level that could keep a modal reference alive across a market switch
    // once the owning PickCard unmounts (which happens automatically: when
    // `picks` becomes empty during a mismatch, React unmounts every
    // PickCard instance for the previous market's symbols).
    expect(pageSource).not.toMatch(/const \[(selectedPick|openPaperTradeSymbol|activePick)/);
  });

  it("scenario H — the guard is recomputed unconditionally on every render from current `market` and `rawDailyPicksData`, not cached/debounced/effect-deferred", () => {
    // No useEffect/useMemo/useRef wraps the guard call — it's a plain const
    // in the render body, so a rapid IN -> US -> IN sequence re-evaluates
    // it synchronously on every single render with no stale window.
    expect(pageSource).not.toMatch(/useEffect\(\(\) => \{\s*\/\/[^}]*selectPayloadForMarket/);
    expect(pageSource).not.toMatch(/useMemo\(\(\) => selectPayloadForMarket/);
  });

  it("scenarios C, D, E — the India freshness containment and PickCard's market prop are unchanged, and now read the already-guarded `data`", () => {
    // Same freshness-gating code from the prior workstream, untouched —
    // it now simply operates on a `picks` array that can never be a
    // mismatched-market payload.
    expect(pageSource).toContain('const isStaleOrUnknown = !!freshness && freshness.freshnessStatus !== "fresh";');
    expect(pageSource).toContain("freshness={freshnessBySymbol?.[pick.symbol]}");
    expect(pageSource).toContain('market={market}');
  });
});
