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

const FALLBACK_REASON = "Reason unavailable was not supplied by this report.";

function plainLanguageReason(claim: PostmortemClaim): string {
  if (claim.evidence_class !== "INSUFFICIENT_EVIDENCE") return "";
  const parts = [...claim.missing_evidence, ...claim.limitations].filter(Boolean);
  if (parts.length > 0) return parts.join("; ");
  return FALLBACK_REASON;
}

function FactorClaimRow({
  claim,
  evidenceById,
}: {
  claim: PostmortemClaim;
  evidenceById: Map<string, EvidenceItem>;
}) {
  const [open, setOpen] = useState(false);
  const { title, curated } = getFactorLabel(claim.report_section, claim.factor);
  const reason = plainLanguageReason(claim);

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
              EVIDENCE_CLASS_STYLE[claim.evidence_class]
            )}
          >
            {EVIDENCE_CLASS_LABEL[claim.evidence_class] ?? claim.evidence_class}
          </div>
        </div>
        <span className="text-gray-500 text-xs shrink-0 mt-0.5">{open ? "Hide" : "Details"}</span>
      </button>

      {claim.evidence_class === "INSUFFICIENT_EVIDENCE" && (
        <div className="text-xs text-gray-500 mt-1 break-words">{reason}</div>
      )}
      {claim.evidence_class !== "INSUFFICIENT_EVIDENCE" && (
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
}: {
  section: string;
  claims: PostmortemClaim[];
  evidenceById: Map<string, EvidenceItem>;
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
      {claims.map((c) => (
        <FactorClaimRow key={c.claim_id} claim={c} evidenceById={evidenceById} />
      ))}
    </div>
  );
}

export interface WhatYouCanLearnBuckets {
  confirmed: PostmortemClaim[];
  supportedNotProven: PostmortemClaim[];
  notEstablished: PostmortemClaim[];
  dataNeeded: PostmortemClaim[];
}

export function bucketClaimsForWhatYouCanLearn(claims: PostmortemClaim[]): WhatYouCanLearnBuckets {
  const buckets: WhatYouCanLearnBuckets = {
    confirmed: [],
    supportedNotProven: [],
    notEstablished: [],
    dataNeeded: [],
  };
  for (const c of claims) {
    if (c.evidence_class === "MECHANICALLY_VERIFIED" || c.evidence_class === "DIRECTLY_OBSERVED") {
      buckets.confirmed.push(c);
    } else if (c.evidence_class === "EVIDENCE_SUPPORTED") {
      buckets.supportedNotProven.push(c);
    } else if (c.evidence_class === "CONFLICTING_EVIDENCE") {
      buckets.notEstablished.push(c);
    } else {
      buckets.dataNeeded.push(c);
    }
  }
  return buckets;
}

function WhatYouCanLearn({ claims }: { claims: PostmortemClaim[] }) {
  const buckets = bucketClaimsForWhatYouCanLearn(claims);
  const rows: { label: string; items: PostmortemClaim[]; className: string }[] = [
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
                {row.items.map((c) => (
                  <li key={c.claim_id} className="break-words">
                    {getFactorLabel(c.report_section, c.factor).title}
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
}: {
  claims: PostmortemClaim[];
  evidenceById: Map<string, EvidenceItem>;
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
      <WhatYouCanLearn claims={claims} />
      {Array.from(bySection.entries()).map(([section, sectionClaims]) => (
        <SectionGroup key={section} section={section} claims={sectionClaims} evidenceById={evidenceById} />
      ))}
    </div>
  );
}
