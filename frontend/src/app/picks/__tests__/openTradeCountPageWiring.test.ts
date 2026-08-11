import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Paper Trading Repeat-Buy Awareness — Phase 1 (Daily Picks Open-Trade
// Count). `DailyPicksPage` (frontend/src/app/picks/page.tsx) mounts through
// live data-fetching (useQuery, useRouter, market-status/session-freshness
// checks, INTEGRITY_HOLD_ACTIVE) that other picks-page test files in this
// directory (see validationIntegrityHold.test.ts, crossMarketPayloadGuard
// .test.ts) document is not practically mountable in isolation — so, same
// as those files, this locks in the required page-level wiring as
// source-text assertions against the real file, and
// openTradeCountBadge.test.tsx / paperPortfolioSharedQuery.test.tsx cover
// the actually-mountable behavior (PickCard rendering, N+1-safety, auth
// gating) directly.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Daily Picks page — open-trade count query wiring", () => {
  it("reuses the exact existing paper-portfolio query key and fetch function (no new endpoint)", () => {
    expect(pageSource).toContain('queryKey: ["paper-portfolio", userId]');
    expect(pageSource).toContain("queryFn: () => fetchPaperPortfolio(userId, user?.email)");
  });

  it("gates the portfolio fetch on authentication so unauthenticated users issue zero portfolio requests", () => {
    expect(pageSource).toContain("enabled: !!userId");
  });

  it("derives userId from useAuth(), the same hook used elsewhere in the app for this contract", () => {
    expect(pageSource).toContain('import { useAuth } from "@/lib/AuthContext";');
    expect(pageSource).toContain("const { user } = useAuth();");
    expect(pageSource).toContain('const userId = user?.id ?? "";');
  });

  it("builds the count map via the shared pure helper, not ad-hoc inline aggregation", () => {
    expect(pageSource).toContain(
      "buildOpenTradeCountMap(portfolioForCounts.open_trades)"
    );
    expect(pageSource).toContain(
      'import { buildOpenTradeCountMap, openTradeCountKey } from "@/utils/openTradeCount";'
    );
  });

  it("passes the derived count into every PickCard render (page-level query, not per-card)", () => {
    expect(pageSource).toContain(
      "openTradeCount={openTradeCountMap?.get(openTradeCountKey(market, pick.symbol))}"
    );
  });

  it("does not add a refetchInterval to the paper-portfolio query added for this feature", () => {
    const queryStart = pageSource.indexOf('queryKey: ["paper-portfolio", userId]');
    const queryBlockEnd = pageSource.indexOf("});", queryStart);
    const queryBlock = pageSource.slice(queryStart, queryBlockEnd);
    expect(queryBlock).not.toContain("refetchInterval");
  });

  it("there is exactly one paper-portfolio useQuery call site in this file (page-level, not per-card)", () => {
    const occurrences = (pageSource.match(/queryKey: \["paper-portfolio", userId\]/g) ?? []).length;
    expect(occurrences).toBe(1);
  });
});
