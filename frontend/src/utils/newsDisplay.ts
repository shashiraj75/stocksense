/**
 * News & Sentiment display helpers (Wave 0C display truthfulness).
 *
 * The BACKEND is the single source of truth for whether an article counts
 * toward current sentiment — it annotates each article with
 * `sentiment_eligible` from the central freshness policy. This module never
 * re-derives eligibility from ages or its own thresholds; it only groups by
 * the backend's verdict and makes date rendering crash-safe.
 *
 * Dependency-free on purpose: directly executable by the standalone
 * regression script (frontend/scripts/test-news-display.mjs) in a repo with
 * no frontend test framework.
 */

export interface FreshnessGroupable {
  sentiment_eligible?: boolean;
  company_sentiment_eligible?: boolean;
}

export interface RelevanceGroupedArticles<T extends FreshnessGroupable> {
  /** Fresh AND company-specific — the only articles current company
   *  sentiment may use. */
  companyCurrent: T[];
  /** Fresh but not company-specific (peer/sector/macro/unknown) — recent
   *  context with zero company-sentiment weight. */
  recentContext: T[];
  /** Not freshness-eligible — historical context. */
  historical: T[];
  /** False when no article carries the Wave 0D1 relevance annotation (a
   *  cached pre-0D1 payload) — the UI must then fall back to the Release 8
   *  freshness-only grouping and make no company-inclusion claims. */
  hasRelevanceData: boolean;
}

/**
 * Wave 0D1 three-way grouping. Grouping keys ONLY on the backend's
 * annotations — no relevance or freshness policy exists in the frontend.
 */
export function groupArticlesByRelevance<T extends FreshnessGroupable>(
  articles: T[],
): RelevanceGroupedArticles<T> {
  const hasRelevanceData = articles.some(a => a.company_sentiment_eligible !== undefined);
  if (!hasRelevanceData) {
    return { companyCurrent: [], recentContext: [], historical: [], hasRelevanceData: false };
  }
  return {
    companyCurrent: articles.filter(a => a.company_sentiment_eligible === true),
    recentContext: articles.filter(
      a => a.company_sentiment_eligible !== true && a.sentiment_eligible === true),
    historical: articles.filter(a => a.sentiment_eligible !== true),
    hasRelevanceData: true,
  };
}

export interface GroupedArticles<T extends FreshnessGroupable> {
  /** Fresh eligible articles — the only ones current sentiment may use. */
  current: T[];
  /** Stale/undated/future-dated articles — context only, zero decision weight. */
  historical: T[];
  /** False when the payload predates the eligibility annotation (e.g. a
   *  cached response from an older deployment) — the UI must then render a
   *  single ungrouped list and make no inclusion/exclusion claims at all,
   *  rather than guessing. */
  hasEligibilityData: boolean;
}

export function groupArticlesByEligibility<T extends FreshnessGroupable>(
  articles: T[],
): GroupedArticles<T> {
  const hasEligibilityData = articles.some(a => a.sentiment_eligible !== undefined);
  if (!hasEligibilityData) return { current: [], historical: [], hasEligibilityData: false };
  return {
    current: articles.filter(a => a.sentiment_eligible === true),
    historical: articles.filter(a => a.sentiment_eligible !== true),
    hasEligibilityData: true,
  };
}

/**
 * Truthful basis line for the current-company-news group (Wave 0D3).
 * When the backend reports fewer unique events than articles (duplicate
 * coverage), say so; when they match — or when event metadata is absent
 * (legacy payload) — keep the concise article wording and make no
 * duplicate-story claim.
 */
export function formatCompanyNewsBasis(
  eventCount: number | undefined,
  articleCount: number,
): string {
  if (typeof eventCount === "number" && eventCount > 0 && eventCount !== articleCount) {
    return `Based on ${eventCount} recent company-news event${eventCount !== 1 ? "s" : ""} across ${articleCount} articles.`;
  }
  return `Based on ${articleCount} recent company-specific article${articleCount !== 1 ? "s" : ""}.`;
}

/**
 * Parse an article's publication date for display. Returns null for
 * missing/malformed values instead of an Invalid Date object — so the UI
 * can show "publication date unavailable" and date-fns is never handed an
 * invalid value (formatDistanceToNow throws RangeError on those).
 */
export function parseArticleDate(raw: string | null | undefined): Date | null {
  if (!raw) return null;
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}
