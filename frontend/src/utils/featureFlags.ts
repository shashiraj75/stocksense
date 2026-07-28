/**
 * Feature gates — PR #32 pre-merge correction.
 *
 * Fail-closed frontend presentation gate for Sprint 1's daily Trade
 * Postmortem surface (nav link + /postmortem page + its API calls) —
 * the browser-side mirror of the backend's own TRADE_POSTMORTEM_DAILY_ENABLED
 * flag. This does NOT replace that backend gate; it is a SECOND,
 * independent gate so that with no environment configured, the browser
 * never even issues a request to /api/paper-trading/postmortem/daily —
 * mirrors the exact "third gate" rationale already established in this
 * codebase by src/utils/marketLeadership.ts's isMarketLeadershipUiEnabled.
 *
 * Sprint 1 is an evidence-governance foundation, not the complete
 * investor-facing product — see backend/services/postmortem/
 * evidence_attribution.py's module docstring for what's intentionally
 * still missing (close-to-report orchestration, exit snapshots, persisted
 * reports, price-path/market/sector/news evidence, export).
 *
 * A plain function (not a module-level constant computed once at import)
 * so it reads `process.env` fresh on every call — this is what makes it
 * directly testable with `process.env.X = "..."` per test case, and
 * matches this codebase's own established gate-function convention.
 * Next.js still inlines `NEXT_PUBLIC_*` into the client bundle at build
 * time regardless of whether the read happens inside a function or at
 * module scope, so this changes nothing about production behavior.
 *
 * Accepts the same broader truthy set as the backend's own
 * TRADE_POSTMORTEM_DAILY_ENABLED (`"1"`/`"true"`/`"yes"`/`"on"`, case-
 * insensitive, trimmed) rather than isMarketLeadershipUiEnabled's
 * stricter exact-`"1"` check — kept consistent with the backend flag for
 * this specific feature, not with the other frontend gate's convention.
 * Missing, empty, or any unrecognized value resolves to disabled.
 */
export function isTradePostmortemDailyEnabled(): boolean {
  const raw = process.env.NEXT_PUBLIC_TRADE_POSTMORTEM_DAILY_ENABLED;
  if (!raw) return false;
  const normalized = raw.trim().toLowerCase();
  return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on";
}
