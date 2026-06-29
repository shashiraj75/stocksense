"use client";
import { Lock, Flag, AlertTriangle, Info, ShieldQuestion } from "lucide-react";
import clsx from "clsx";
import { Prediction, RecommendationConsolidation, getValidRecommendationConsolidation } from "@/utils/api";
import { DisclosurePanel } from "@/components/DisclosurePanel";

// Epic 005 — Recommendation Consolidation Intelligence's additive, read-only
// "Evidence Summary" consumer (Sprints #009-#011 design, implemented Sprint
// #012). Renders ONLY backend-provided RCI meaning — no field here is
// derived from raw business_quality/financial_strength/growth_intelligence/
// valuation_intelligence values, no second signal or confidence percentage
// is ever shown, and the whole section renders nothing at all whenever
// `getValidRecommendationConsolidation` returns null (RCI absent, disabled,
// malformed, or an unsupported future contract version) — never an error,
// placeholder, or loading state, per Sprint #011 §3A/§9 scenario 1/2.
//
// Per Sprint #011's confirmed contract gap: `coverage_notices` /
// `unresolved_risk_flags` / `material_warnings` have no stable per-item
// identifier (unlike `conflicts`, which has `conflict_id`). De-duplication
// below is therefore scoped to the current response only (a plain `Set` on
// the exact strings already returned for this one fetch), never across
// stocks or sessions, per that sprint's explicit instruction not to rely on
// cross-response text matching.
//
// A genuine backend-contract gap, found while implementing this component,
// not invented around: the backend never serializes a dedicated
// "feature-disabled" notice (e.g. for Valuation Intelligence while its
// kill switch is off) anywhere in `RecommendationConsolidationResponse` —
// `FEATURE_DISABLED` status is used only internally to gate CP-02/CP-03
// conflict detection (`recommendation_consolidation_engine.py`). There is
// therefore no field this component can safely render for that state today
// without inventing frontend-only copy describing backend logic it cannot
// see — so this component intentionally renders nothing for that case,
// the same safe default as RCI being fully absent. See Sprint #012's
// release report for the recommended minimal backend addition.

const MAX_VISIBLE_ITEMS = 3;

function headlineFor(rci: RecommendationConsolidation): string {
  if (rci.active_gates.length > 0) return "Existing gate blocks the thesis";
  if (rci.unresolved_risk_flags.length > 0 || rci.material_warnings.length > 0) return "Important caution present";
  if (rci.thesis_state === "mixed" || rci.thesis_state === "conflicted") return "Evidence is mixed";
  if (rci.thesis_state === "insufficient_evidence" || rci.evidence_completeness_pct == null || rci.evidence_completeness_pct < 50) {
    return "Limited evidence available";
  }
  return "Evidence broadly aligned";
}

function relativeTime(isoTimestamp: string): string {
  const ms = Date.now() - new Date(isoTimestamp).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function EvidenceList({
  items,
  maxVisible = MAX_VISIBLE_ITEMS,
  noun,
}: {
  items: string[];
  maxVisible?: number;
  noun: string;
}) {
  if (items.length === 0) return null;
  const visible = items.slice(0, maxVisible);
  const remaining = items.length - visible.length;
  return (
    <div className="space-y-1">
      <ul className="space-y-1 text-xs text-gray-300 list-disc list-inside">
        {visible.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
      {remaining > 0 && (
        <DisclosurePanel label={`Show ${remaining} more ${noun}${remaining === 1 ? "" : "s"}`}>
          <ul className="space-y-1 text-xs text-gray-300 list-disc list-inside">
            {items.slice(maxVisible).map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </DisclosurePanel>
      )}
    </div>
  );
}

function EvidenceSummaryNotice({
  icon: Icon,
  tone,
  ariaLabelPrefix,
  children,
}: {
  icon: typeof Lock;
  tone: "critical" | "caution" | "neutral";
  ariaLabelPrefix: string;
  children: React.ReactNode;
}) {
  const toneClasses =
    tone === "critical"
      ? "bg-bear/8 border-bear/25 text-red-300"
      : tone === "caution"
      ? "bg-yellow-500/8 border-yellow-500/25 text-yellow-300"
      : "bg-white/[0.04] border-white/[0.08] text-gray-300";
  return (
    <div
      role="note"
      aria-label={`${ariaLabelPrefix}`}
      className={clsx("flex items-start gap-2 rounded-xl px-3 py-2 border text-xs", toneClasses)}
    >
      <Icon size={14} className="shrink-0 mt-0.5" />
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

export function EvidenceSummary({ prediction }: { prediction: Prediction | null | undefined }) {
  const rci = getValidRecommendationConsolidation(prediction);
  if (!rci) return null;

  const headline = headlineFor(rci);
  // De-duplicated within this single response only — never across stocks
  // or sessions (Sprint #011 §6's confirmed contract limitation).
  const coverageNotices = Array.from(new Set(rci.coverage_notices));

  return (
    <div className="mt-3 pt-3 border-t border-white/[0.06] space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <ShieldQuestion size={13} className="text-brand-500" />
          <span className="uppercase tracking-wide font-semibold">Evidence Summary</span>
        </div>
        <span className="text-[10px] text-gray-500" title={rci.computed_at}>
          {rci.is_snapshot ? "Snapshot" : "Live"} · {relativeTime(rci.computed_at)}
        </span>
      </div>

      <h3 className="text-sm font-semibold text-white">{headline}</h3>
      <p className="text-xs text-gray-400 leading-relaxed">{rci.narrative}</p>

      {/* Active gates — always visible, never collapsed (Sprint #011 §4 rule 1). */}
      {rci.active_gates.map((gate, i) => (
        <EvidenceSummaryNotice
          key={`gate-${i}`}
          icon={Lock}
          tone="critical"
          ariaLabelPrefix={`Active gate: ${gate}`}
        >
          <span className="font-medium">{gate}</span>
        </EvidenceSummaryNotice>
      ))}

      {/* Material warnings — always visible, never collapsed (Sprint #011 §8 rule). */}
      {rci.material_warnings.map((warning, i) => (
        <EvidenceSummaryNotice
          key={`warning-${i}`}
          icon={AlertTriangle}
          tone="caution"
          ariaLabelPrefix={`Caution: ${warning}`}
        >
          <span>{warning}</span>
        </EvidenceSummaryNotice>
      ))}

      {/* Unresolved risk flags — distinct icon/tone from an active gate. */}
      {rci.unresolved_risk_flags.map((flag, i) => (
        <EvidenceSummaryNotice
          key={`flag-${i}`}
          icon={Flag}
          tone="caution"
          ariaLabelPrefix={`Unresolved risk flag, not currently enforced: ${flag}`}
        >
          <span>{flag}</span>
        </EvidenceSummaryNotice>
      ))}

      <DisclosurePanel label="Show evidence detail">
        <div className="space-y-3 pl-0.5">
          {rci.supporting_evidence.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold text-gray-500 mb-1">Supports this view</div>
              <EvidenceList items={rci.supporting_evidence} noun="supporting factor" />
            </div>
          )}

          {rci.opposing_evidence.length > 0 && (
            <div>
              <div className="text-[11px] font-semibold text-gray-500 mb-1">Challenges this view</div>
              <EvidenceList items={rci.opposing_evidence} noun="challenging factor" />
            </div>
          )}

          {rci.conflicts.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[11px] font-semibold text-gray-500 mb-1">Notable patterns</div>
              {rci.conflicts.map((c) => (
                <div key={c.conflict_id} className="text-xs text-gray-300">
                  <span className="font-medium">{c.headline}.</span> {c.narrative}
                </div>
              ))}
            </div>
          )}

          {coverageNotices.length > 0 && (
            <DisclosurePanel label="Coverage">
              <div className="space-y-1.5">
                {coverageNotices.map((notice, i) => (
                  <EvidenceSummaryNotice key={i} icon={Info} tone="neutral" ariaLabelPrefix={`Platform coverage note: ${notice}`}>
                    <span>{notice}</span>
                  </EvidenceSummaryNotice>
                ))}
              </div>
            </DisclosurePanel>
          )}

          <div className="text-[10px] text-gray-500 pt-1">
            {rci.engine_agreement} · Explanation confidence: {rci.explanation_confidence_category}
          </div>
        </div>
      </DisclosurePanel>
    </div>
  );
}
