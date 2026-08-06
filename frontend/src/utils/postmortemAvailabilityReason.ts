// Sprint 3b (completion pass) — deterministic availability-reason resolver
// for MFE / MAE / target-touch / stop-touch. Derives ONE of the governed
// reason categories (or the literal fallback sentence) purely from fields
// the backend's CurrentReportReadResponse actually returns — never
// invented, never guessed from claim text, never inferred from P&L or
// exit outcome.
//
// PRECEDENCE (first matching tier wins — structured fields always beat
// text matching):
//   1. The value itself is present -> "Available".
//   2. report.availability exact value (the lifecycle field — distinct
//      from report.status, which is a completeness value like
//      LIMITED_EVIDENCE and does not gate this tier). A non-READY
//      availability means the value was never computable at all.
//   3. source_manifest structured fields — currently only
//      `exit_trigger_timing_verification`, a real typed enum
//      (backend/services/postmortem/deterministic.py's
//      AutoCloseTimingEvidence: CLIENT_REPORTED_UNVERIFIED | SERVER_VERIFIED)
//      — checked by EXACT value equality, never substring matching.
//   4. Exact-string lookup against the literal evidence_gaps / warnings /
//      price_path_limitations strings this codebase is known to emit
//      today (see the source citations beside EXACT_TEXT_TABLE below).
//      Exact equality, not regex — an incidental wording change to one of
//      these strings will fail to match and fall through to tier 5/6
//      rather than silently guessing wrong.
//   5. A small, narrowly-bounded substring check for the ONE known
//      dynamically-templated message (price_path_acquisition.py's
//      "requested window {a}..{b} but provider only returned bars for
//      {c}..{d}") that can never be an exact-match table entry because the
//      dates vary per trade.
//   6. Final safe fallback.
export type AvailabilityReasonCategory =
  | "Available"
  | "Not captured at entry"
  | "Provider returned incomplete coverage"
  | "No compatible price bars"
  | "Holding-period sessions were missing"
  | "Boundary could not be verified"
  | "Client-reported only"
  | "Historical evidence unavailable"
  | "Not applicable"
  | "Reason unavailable was not supplied by this report.";

const FALLBACK: AvailabilityReasonCategory = "Reason unavailable was not supplied by this report.";

// Tier 4 — exact strings this codebase is known to emit today. Keys are
// the LITERAL text (or literal prefix, see PREFIX_TABLE below) the backend
// writes into evidence_gaps / warnings / price_path_limitations. Source
// citations are the file:line where each string is authored, so a future
// wording change is easy to re-verify against this table.
const EXACT_TEXT_TABLE: Record<string, AvailabilityReasonCategory> = {
  // generation_service.py:133 / price_path_eligibility.py:233
  "no entry snapshot exists for this trade — entry-time evidence is unavailable": "Not captured at entry",
  "no entry snapshot exists for this trade — entry-time thesis evidence is unavailable": "Not captured at entry",
  "no entry snapshot exists for this trade": "Not captured at entry",
  // generation_service.py:141
  "entry evidence completeness is LIMITED": "Not captured at entry",
  // generation_service.py:136 / price_path_eligibility.py:246 / exit_evidence.py:78
  "no exit snapshot exists for this trade — this is a historical trade closed before":
    "Historical evidence unavailable",
  "no exit snapshot exists for this trade — it closed before Sprint 2's durable close lifecycle existed":
    "Historical evidence unavailable",
  // current_report_generation.py:219
  "no compatible price-path evidence is available for this trade": "No compatible price bars",
  // price_path_calculator.py:169
  "no bars exist strictly between the entry and exit sessions — a same-day or":
    "No compatible price bars",
  // price_path_acquisition.py:924
  "no valid bars were returned by the provider for the requested window": "Provider returned incomplete coverage",
  // governed_price_path_conclusions.py:315 / :430
  "no compatible crossing of the supplied value was observed in the acquired evidence window":
    "Boundary could not be verified",
  "no compatible crossing of either supplied value was safely observed": "Boundary could not be verified",
  // evidence_attribution.py:348 / :688
  "client-reported at Buy time; no server-side corroboration exists for this field": "Client-reported only",
};

// Same category, matched as a PREFIX (the backend appends a trailing
// clause after these) — still exact-prefix, not a loose regex.
const PREFIX_TABLE: { prefix: string; category: AvailabilityReasonCategory }[] = [
  { prefix: "no exit snapshot exists for this trade", category: "Historical evidence unavailable" },
  { prefix: "no entry snapshot exists for this trade", category: "Not captured at entry" },
  { prefix: "no bars exist strictly between the entry and exit sessions", category: "No compatible price bars" },
  {
    prefix: "no compatible crossing of the supplied value was observed in the acquired evidence window",
    category: "Boundary could not be verified",
  },
];

// Tier 5 — the one known dynamically-templated message
// (price_path_acquisition.py:949) that can never be an exact-match table
// entry because the dates vary per trade. Bounded to this single known
// substring, not a broad pattern.
const BOUNDED_DYNAMIC_SUBSTRING = "but provider only returned bars for";

function matchExactOrPrefix(text: string): AvailabilityReasonCategory | null {
  if (EXACT_TEXT_TABLE[text]) return EXACT_TEXT_TABLE[text];
  for (const { prefix, category } of PREFIX_TABLE) {
    if (text.startsWith(prefix)) return category;
  }
  return null;
}

export interface AvailabilityReasonInput {
  /** The value itself — if non-null, this factor is Available regardless of any limitation text. */
  value: unknown;
  /** report.availability (e.g. "READY", "PROCESSING") — the lifecycle field, distinct from report.status. */
  reportAvailability?: string | null;
  /** source_manifest.exit_trigger_timing_verification — a real typed enum, checked by exact equality. */
  exitTriggerTimingVerification?: string | null;
  pricePathLimitations: string[];
  evidenceGaps: string[];
  warnings: string[];
}

export function resolveAvailabilityReason(input: AvailabilityReasonInput): AvailabilityReasonCategory {
  // Tier 1 — the value itself.
  if (input.value !== null && input.value !== undefined) return "Available";

  // Tier 2 — report-level availability. A price-path field can never have
  // been computed for a report that never reached READY at all.
  if (input.reportAvailability && input.reportAvailability !== "READY") {
    return "Not applicable";
  }

  // Tier 3 — structured source_manifest field, exact enum equality.
  if (input.exitTriggerTimingVerification === "CLIENT_REPORTED_UNVERIFIED") {
    return "Client-reported only";
  }

  // Tier 4 — exact/prefix text-table match against real governed strings.
  const haystack = [...input.pricePathLimitations, ...input.evidenceGaps, ...input.warnings];
  for (const text of haystack) {
    const match = matchExactOrPrefix(text.trim());
    if (match) return match;
  }

  // Tier 5 — the one known bounded dynamic-template substring.
  for (const text of haystack) {
    if (text.includes(BOUNDED_DYNAMIC_SUBSTRING)) return "Provider returned incomplete coverage";
  }

  // Tier 6 — final safe fallback. Never invented.
  return FALLBACK;
}

/**
 * TARGET_HIT with target_touch === null must never be described as a
 * contradiction — it is a gap between a client-reported close event and
 * independently-unverifiable price-path evidence. This helper returns the
 * required exact framing whenever that specific combination is detected.
 */
export function isUnverifiedTargetHitCloseCase(exitMechanism: string | null, targetTouch: boolean | null): boolean {
  return exitMechanism === "TARGET_HIT" && targetTouch === null;
}

export const UNVERIFIED_TARGET_HIT_EXPLANATION =
  "This trade closed as a reported target hit, but the price path evidence needed to independently verify " +
  "that the target level was actually touched is not available. This is a gap between a client-reported " +
  "close event and independently-unverifiable price-path evidence — not a contradiction.";
