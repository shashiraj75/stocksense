"use client";
// Sprint 3b — Layer 2 (Investor Explanation) grouped-claim UI. Renders
// EVERY claim in report.claims[] exactly once, grouped by report_section,
// each with a named factor title (never a raw/unnamed row), a text+icon
// evidence-class label (never color-only), a plain-language availability
// reason, and a "What You Can Learn" rollup. Repeated INSUFFICIENT_EVIDENCE
// claims are summarized with an overview count but every one is still
// individually listed and expandable.
import { useState } from "react";
import clsx from "clsx";
import type { PostmortemClaim, EvidenceItem } from "@/utils/api";
import { getFactorLabel, getReportSectionLabel } from "@/utils/postmortemFactorLabels";
import { resolveClaimReason } from "@/utils/postmortemClaimReasons";
import type { PriceFieldAssessment, PriceFieldFactor } from "@/utils/postmortemPriceFieldAssessment";

// Maps a governed claim's `factor` string to the corresponding
// PriceFieldFactor — both the legacy `price_path::mfe/mae/target_touch/
// stop_touch` (1.1.0) and current `governed_price_path::target_touch/
// stop_touch` (1.2.0) sections use these same bare factor strings.
const CLAIM_FACTOR_TO_PRICE_FIELD: Record<string, PriceFieldFactor> = {
  mfe: "MFE", mae: "MAE", target_touch: "TARGET_TOUCH", stop_touch: "STOP_TOUCH",
};

// Reverse of the above — used to look up the SAME curated label a claims-
// based row would use, for a price-field factor with no matching claim
// (e.g. MFE/MAE) so its standalone "What You Can Learn" item has an
// identical title to what the factor section would show if a claim did
// exist. Uses the current 1.2.0 governed_price_path section labels for
// target/stop, and the price_path section labels for MFE/MAE.
const PRICE_FIELD_TO_LABEL_LOOKUP: Record<PriceFieldFactor, { section: string; factor: string }> = {
  MFE: { section: "price_path", factor: "mfe" },
  MAE: { section: "price_path", factor: "mae" },
  TARGET_TOUCH: { section: "governed_price_path", factor: "target_touch" },
  STOP_TOUCH: { section: "governed_price_path", factor: "stop_touch" },
};

const EVIDENCE_CLASS_STYLE: Record<string, string> = {
  MECHANICALLY_VERIFIED: "text-bull",
  DIRECTLY_OBSERVED: "text-gray-200",
  EVIDENCE_SUPPORTED: "text-brand-500",
  CONFLICTING_EVIDENCE: "text-neutral",
  INSUFFICIENT_EVIDENCE: "text-gray-500",
};

// Text/icon labels — never rely on color alone to convey evidence class.
const EVIDENCE_CLASS_LABEL: Record<string, string> = {
  MECHANICALLY_VERIFIED: "✓ Verified fact",
  DIRECTLY_OBSERVED: "● Direct observation",
  EVIDENCE_SUPPORTED: "◐ Supported interpretation",
  CONFLICTING_EVIDENCE: "⚠ Conflicting evidence",
  INSUFFICIENT_EVIDENCE: "○ Insufficient evidence",
};

// For the four price-path factors, the visible Layer-2 badge is derived
// from the SAME PriceFieldAssessment as everything else — never from the
// underlying claim.evidence_class, which can legitimately be
// MECHANICALLY_VERIFIED/DIRECTLY_OBSERVED while genuinely describing an
// unavailable metric (see postmortemPriceFieldAssessment.ts). Showing
// "DIRECT OBSERVATION" for a NOT_ESTABLISHED price field misleadingly
// implied the metric itself was confirmed — a real defect found during
// preview QA. The original claim.evidence_class remains unmodified and
// visible in the expandable Layer-3 detail panel below.
const LEARNING_CLASSIFICATION_STYLE: Record<string, string> = {
  CONFIRMED: "text-bull",
  SUPPORTED_BUT_NOT_PROVEN: "text-brand-500",
  NOT_ESTABLISHED: "text-neutral",
  DATA_NEEDED: "text-gray-500",
};

const LEARNING_CLASSIFICATION_BADGE: Record<string, string> = {
  CONFIRMED: "✓ Verified",
  SUPPORTED_BUT_NOT_PROVEN: "◐ Supported but not proven",
  NOT_ESTABLISHED: "○ Not established",
  DATA_NEEDED: "○ Data needed",
};

function plainLanguageReason(claim: PostmortemClaim, priceFieldAssessment?: PriceFieldAssessment): string {
  if (claim.evidence_class !== "INSUFFICIENT_EVIDENCE") return "";
  // Single-source-of-truth: for the four price-path factors, use the SAME
  // assessment object the summary card and "What You Can Learn" consume —
  // never recompute independently. For every other factor, use the
  // curated claim-reason resolver. Deliberately does NOT surface
  // claim.missing_evidence / claim.limitations directly — that raw
  // governed text (snake_case identifiers, internal phrasing like "this
  // codebase" or a sprint reference) leaked into the investor-facing
  // layer in an earlier version, a real defect found during preview QA.
  // The exact original text remains available verbatim in the expandable
  // Layer-3 detail panel below, unmutated.
  if (priceFieldAssessment) return priceFieldAssessment.explanation;
  return resolveClaimReason({ reportSection: claim.report_section, factor: claim.factor });
}

function FactorClaimRow({
  claim,
  evidenceById,
  priceFieldAssessment,
}: {
  claim: PostmortemClaim;
  evidenceById: Map<string, EvidenceItem>;
  priceFieldAssessment?: PriceFieldAssessment;
}) {
  const [open, setOpen] = useState(false);
  const { title, curated } = getFactorLabel(claim.report_section, claim.factor);
  const reason = plainLanguageReason(claim, priceFieldAssessment);

  return (
    <div className="py-2 border-b border-dark-border/50 last:border-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="w-full text-left flex items-start justify-between gap-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded"
      >
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-200 break-words" data-testid="factor-title">
            {title}
            {!curated && (
              <span className="ml-1 text-[10px] text-gray-500" title="Missing a curated label — humanized automatically">
                (uncurated)
              </span>
            )}
          </div>
          <div
            className={clsx(
              "text-[11px] font-semibold uppercase mt-0.5",
              priceFieldAssessment
                ? LEARNING_CLASSIFICATION_STYLE[priceFieldAssessment.learningClassification]
                : EVIDENCE_CLASS_STYLE[claim.evidence_class]
            )}
          >
            {priceFieldAssessment
              ? LEARNING_CLASSIFICATION_BADGE[priceFieldAssessment.learningClassification]
              : (EVIDENCE_CLASS_LABEL[claim.evidence_class] ?? claim.evidence_class)}
          </div>
        </div>
        <span className="text-gray-500 text-xs shrink-0 mt-0.5">{open ? "Hide" : "Details"}</span>
      </button>

      {priceFieldAssessment && priceFieldAssessment.explanation && (
        // Single-source-of-truth override — a price-path factor always
        // displays the SAME assessment explanation the summary card uses,
        // regardless of the underlying claim's evidence_class (a
        // MECHANICALLY_VERIFIED/DIRECTLY_OBSERVED claim can still
        // genuinely describe an unavailable metric — see
        // postmortemPriceFieldAssessment.ts).
        <div className="text-xs text-gray-400 mt-1 break-words">{priceFieldAssessment.explanation}</div>
      )}
      {!priceFieldAssessment && claim.evidence_class === "INSUFFICIENT_EVIDENCE" && (
        <div className="text-xs text-gray-500 mt-1 break-words">{reason}</div>
      )}
      {!priceFieldAssessment && claim.evidence_class !== "INSUFFICIENT_EVIDENCE" && (
        <div className="text-xs text-gray-400 mt-1 break-words">{claim.claim_text}</div>
      )}

      {claim.contradiction_flags.length > 0 && (
        <div className="text-[11px] text-bear mt-1 break-words">
          <span className="font-semibold">Contradiction (not merely a limitation): </span>
          {claim.contradiction_flags.join("; ")}
        </div>
      )}

      {open && (
        <div className="mt-2 rounded-md border border-dark-border bg-black/20 p-2.5 text-[11px] space-y-1.5">
          <div className="text-gray-500">Original claim text: <span className="text-gray-300">{claim.claim_text}</span></div>
          <div className="text-gray-500">
            Section <span className="text-gray-300">{claim.report_section}</span> · Factor{" "}
            <span className="text-gray-300">{claim.factor}</span> · Rule{" "}
            <span className="text-gray-300">{claim.rule_id}</span> v{claim.rule_version} · confidence{" "}
            <span className="text-gray-300">{claim.confidence_band.replace(/_/g, " ")}</span>
          </div>
          {claim.supporting_evidence_ids.length > 0 && (
            <div>
              <span className="text-gray-500">Supporting: </span>
              {claim.supporting_evidence_ids.map((id) => {
                const ev = evidenceById.get(id);
                return (
                  <span key={id} className="text-gray-300 mr-2 break-words">
                    {ev ? `${ev.name}=${String(ev.value)}` : id}
                  </span>
                );
              })}
            </div>
          )}
          {claim.opposing_evidence_ids.length > 0 && (
            <div>
              <span className="text-gray-500">Opposing: </span>
              {claim.opposing_evidence_ids.map((id) => {
                const ev = evidenceById.get(id);
                return (
                  <span key={id} className="text-bear mr-2 break-words">
                    {ev ? `${ev.name}=${String(ev.value)}` : id}
                  </span>
                );
              })}
            </div>
          )}
          {claim.missing_evidence.length > 0 && (
            <div>
              <span className="text-gray-500">Missing evidence: </span>
              <span className="text-gray-400 break-words">{claim.missing_evidence.join("; ")}</span>
            </div>
          )}
          {claim.limitations.length > 0 && (
            <div>
              <span className="text-gray-500">Limitations: </span>
              <span className="text-gray-400 break-words">{claim.limitations.join("; ")}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SectionGroup({
  section,
  claims,
  evidenceById,
  priceFieldAssessments,
}: {
  section: string;
  claims: PostmortemClaim[];
  evidenceById: Map<string, EvidenceItem>;
  priceFieldAssessments?: Record<PriceFieldFactor, PriceFieldAssessment>;
}) {
  const insufficientCount = claims.filter((c) => c.evidence_class === "INSUFFICIENT_EVIDENCE").length;
  return (
    <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-2 mb-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-1">{getReportSectionLabel(section)}</h3>
      {insufficientCount > 0 && (
        <p className="text-[11px] text-gray-500 mb-1">
          {insufficientCount} factor{insufficientCount === 1 ? "" : "s"} in this section could not be assessed
          reliably — each is still listed by name below.
        </p>
      )}
      {claims.map((c) => {
        const priceField = CLAIM_FACTOR_TO_PRICE_FIELD[c.factor];
        const assessment = priceField && priceFieldAssessments ? priceFieldAssessments[priceField] : undefined;
        return (
          <FactorClaimRow key={c.claim_id} claim={c} evidenceById={evidenceById} priceFieldAssessment={assessment} />
        );
      })}
    </div>
  );
}

// Presentation-only learning item — NOT a governed claim, NOT persisted,
// NOT sent to the backend. Built either from a real PostmortemClaim (the
// common case) or, for a price-path factor with no matching claim row at
// all (e.g. MFE/MAE frequently have no separate claim in a given report),
// directly from that factor's PriceFieldAssessment. Never a synthetic
// governed claim — this exists purely so "What You Can Learn" can list a
// factor whose assessment exists even when report.claims[] has no
// corresponding row, without inventing claim data.
export interface LearningItem {
  key: string;
  title: string;
  classification: "CONFIRMED" | "SUPPORTED_BUT_NOT_PROVEN" | "NOT_ESTABLISHED" | "DATA_NEEDED";
  sourceType: "claim" | "price_field_assessment";
}

export interface WhatYouCanLearnBuckets {
  confirmed: LearningItem[];
  supportedNotProven: LearningItem[];
  notEstablished: LearningItem[];
  dataNeeded: LearningItem[];
}

const LEARNING_CLASSIFICATION_TO_BUCKET: Record<string, keyof WhatYouCanLearnBuckets> = {
  CONFIRMED: "confirmed",
  SUPPORTED_BUT_NOT_PROVEN: "supportedNotProven",
  NOT_ESTABLISHED: "notEstablished",
  DATA_NEEDED: "dataNeeded",
};

function evidenceClassToLearningClassification(evidenceClass: string): LearningItem["classification"] {
  if (evidenceClass === "MECHANICALLY_VERIFIED" || evidenceClass === "DIRECTLY_OBSERVED") return "CONFIRMED";
  if (evidenceClass === "EVIDENCE_SUPPORTED") return "SUPPORTED_BUT_NOT_PROVEN";
  if (evidenceClass === "CONFLICTING_EVIDENCE") return "NOT_ESTABLISHED";
  return "DATA_NEEDED";
}

/**
 * Builds the full set of learning items: every governed claim exactly
 * once, PLUS each of the four price-field assessments exactly once —
 * deduplicated by canonical factor identity so a price-path factor with
 * both a governed claim AND an assessment (the common case) is never
 * listed twice. A price-path factor with NO matching claim (e.g. MFE/MAE
 * often have none) still gets its own standalone item so it is never
 * silently omitted from "What You Can Learn".
 */
export function buildLearningItems(
  claims: PostmortemClaim[],
  priceFieldAssessments?: Record<PriceFieldFactor, PriceFieldAssessment>
): LearningItem[] {
  const items: LearningItem[] = [];
  const seenPriceFields = new Set<PriceFieldFactor>();

  for (const c of claims) {
    const priceField = CLAIM_FACTOR_TO_PRICE_FIELD[c.factor];
    const assessment = priceField && priceFieldAssessments ? priceFieldAssessments[priceField] : undefined;
    if (assessment) {
      seenPriceFields.add(priceField);
      items.push({
        key: c.claim_id, title: getFactorLabel(c.report_section, c.factor).title,
        classification: assessment.learningClassification, sourceType: "claim",
      });
    } else {
      items.push({
        key: c.claim_id, title: getFactorLabel(c.report_section, c.factor).title,
        classification: evidenceClassToLearningClassification(c.evidence_class), sourceType: "claim",
      });
    }
  }

  if (priceFieldAssessments) {
    (Object.keys(priceFieldAssessments) as PriceFieldFactor[]).forEach((factor) => {
      if (seenPriceFields.has(factor)) return; // already represented via a governed claim above
      const assessment = priceFieldAssessments[factor];
      const lookup = PRICE_FIELD_TO_LABEL_LOOKUP[factor];
      const label = getFactorLabel(lookup.section, lookup.factor);
      items.push({
        key: `price-field-${factor}`, title: label.title,
        classification: assessment.learningClassification, sourceType: "price_field_assessment",
      });
    });
  }

  return items;
}

function bucketLearningItems(items: LearningItem[]): WhatYouCanLearnBuckets {
  const buckets: WhatYouCanLearnBuckets = {
    confirmed: [], supportedNotProven: [], notEstablished: [], dataNeeded: [],
  };
  for (const item of items) {
    buckets[LEARNING_CLASSIFICATION_TO_BUCKET[item.classification]].push(item);
  }
  return buckets;
}

/** @deprecated kept for existing callers that build items themselves — prefer buildLearningItems + bucketLearningItems. */
export function bucketClaimsForWhatYouCanLearn(
  claims: PostmortemClaim[],
  priceFieldAssessments?: Record<PriceFieldFactor, PriceFieldAssessment>
): WhatYouCanLearnBuckets {
  return bucketLearningItems(buildLearningItems(claims, priceFieldAssessments));
}

function WhatYouCanLearn({
  claims, priceFieldAssessments,
}: {
  claims: PostmortemClaim[];
  priceFieldAssessments?: Record<PriceFieldFactor, PriceFieldAssessment>;
}) {
  const buckets = bucketLearningItems(buildLearningItems(claims, priceFieldAssessments));
  const rows: { label: string; items: LearningItem[]; className: string }[] = [
    { label: "CONFIRMED", items: buckets.confirmed, className: "text-bull" },
    { label: "SUPPORTED BUT NOT PROVEN", items: buckets.supportedNotProven, className: "text-brand-500" },
    { label: "NOT ESTABLISHED", items: buckets.notEstablished, className: "text-neutral" },
    { label: "DATA NEEDED FOR A DEEPER REPORT", items: buckets.dataNeeded, className: "text-gray-500" },
  ];
  return (
    <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-3 mb-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">What You Can Learn</h3>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.label}>
            <div className={clsx("text-[11px] font-semibold uppercase", row.className)}>
              {row.label} ({row.items.length})
            </div>
            {row.items.length > 0 && (
              <ul className="list-disc list-inside text-xs text-gray-400 mt-0.5">
                {row.items.map((item) => (
                  <li key={item.key} className="break-words">
                    {item.title}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Top-level Layer 2 component. Renders every claim exactly once, grouped
 * by report_section, plus the "What You Can Learn" rollup. Callers should
 * assert `document.querySelectorAll('[data-testid="factor-title"]').length
 * === report.claims.length` to verify no claim is dropped or merged.
 */
export function ClaimExplanationLayer({
  claims,
  evidenceById,
  priceFieldAssessments,
}: {
  claims: PostmortemClaim[];
  evidenceById: Map<string, EvidenceItem>;
  /** The single source of truth for MFE/MAE/TARGET_TOUCH/STOP_TOUCH — shared with the summary card and target-hit note in page.tsx. */
  priceFieldAssessments?: Record<PriceFieldFactor, PriceFieldAssessment>;
}) {
  if (claims.length === 0) {
    return <div className="text-xs text-gray-500">No claims are available for this report.</div>;
  }

  const bySection = new Map<string, PostmortemClaim[]>();
  for (const c of claims) {
    const list = bySection.get(c.report_section) ?? [];
    list.push(c);
    bySection.set(c.report_section, list);
  }

  return (
    <div>
      <WhatYouCanLearn claims={claims} priceFieldAssessments={priceFieldAssessments} />
      {Array.from(bySection.entries()).map(([section, sectionClaims]) => (
        <SectionGroup
          key={section} section={section} claims={sectionClaims} evidenceById={evidenceById}
          priceFieldAssessments={priceFieldAssessments}
        />
      ))}
    </div>
  );
}
