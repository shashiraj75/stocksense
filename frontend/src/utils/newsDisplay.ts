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
