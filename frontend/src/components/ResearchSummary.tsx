"use client";
import { FileText } from "lucide-react";
import {
  Prediction,
  ResearchReportEntry,
  ResearchReportSection,
  getValidResearchReport,
} from "@/utils/api";
import { DisclosurePanel } from "@/components/DisclosurePanel";

// AI Equity Research Analyst — Phase 3's additive, read-only "AI Research
// Summary" consumer (Epic 008B; see the Phase 3 UI/Copy Specification).
// Renders ONLY the backend-assembled research_report — no field here is
// derived from raw engine values, no new score/probability/advice
// sentence is ever composed client-side, and the whole section renders
// nothing at all whenever `getValidResearchReport` returns null
// (absent, RESEARCH_ANALYST_V2_ENABLED disabled — the default — the
// backend's fail-open composer omitted it, or an unsupported future
// contract version) — never an error, placeholder, or loading state,
// per the Phase 3 spec §10.
//
// Section titles and render rules below are fixed by the Phase 3 spec §6:
// current_signal_context and disclaimer are never collapsed (the decision
// context and the user-decision boundary must always be visible); the
// other four are collapsed by default via the existing DisclosurePanel
// primitive. A section's `text` is the honest-absence sentence and is
// rendered whenever `entries` is empty; `entries`, when present, are
// rendered with their owner label always visible (Phase 3 spec §7 —
// an unattributed claim is a prohibited failure mode).

const SECTION_TITLES: Record<string, string> = {
  executive_snapshot: "At a Glance",
  current_signal_context: "Current Signal (from Prediction Engine)",
  key_evidence: "Supporting Evidence by Engine",
  risks_and_invalidation: "Risks and What Could Change",
  data_availability: "Data Coverage and Limitations",
  disclaimer: "Disclosures",
};

// Owners that must never be relabeled — QualityFactorsLegacy is
// PredictionEngine's own pre-existing factor, distinct from the Business
// Quality Engine, and must read that way (Phase 3 spec §6, key_evidence row).
const OWNER_LABELS: Record<string, string> = {
  QualityFactorsLegacy: "Quality Factor (legacy)",
};

function ownerLabel(owner?: string): string | null {
  if (!owner) return null;
  return OWNER_LABELS[owner] ?? owner;
}

function formatValue(value: unknown, displayValue?: string): string {
  if (displayValue) return displayValue;
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function EntryRow({ entry }: { entry: ResearchReportEntry }) {
  const owner = ownerLabel(entry.owner);
  return (
    <div
      className="flex items-start justify-between gap-3 text-xs text-gray-300 py-1"
      title={entry.evidence_ids.length ? `Evidence: ${entry.evidence_ids.join(", ")}` : undefined}
    >
      <span className="text-gray-400">
        {entry.label}
        {owner && (
          <span className="ml-1.5 text-[10px] uppercase tracking-wide text-brand-400/80">
            {owner}
          </span>
        )}
      </span>
      <span className="font-medium text-white text-right shrink-0 max-w-[55%] break-words">
        {formatValue(entry.value, entry.display_value)}
      </span>
    </div>
  );
}

function SectionBody({ section }: { section: ResearchReportSection }) {
  return (
    <div className="space-y-1">
      {section.entries.map((entry, i) => (
        <EntryRow key={`${section.section_id}-${i}`} entry={entry} />
      ))}
      {/* The honest-absence / always-present text — rendered whenever the
          backend supplied one, never inferred or composed client-side. */}
      {section.text && (
        <p className="text-xs text-gray-400 leading-relaxed pt-1">{section.text}</p>
      )}
    </div>
  );
}

const ALWAYS_EXPANDED = new Set(["current_signal_context", "disclaimer"]);

export function ResearchSummary({ prediction }: { prediction: Prediction | null | undefined }) {
  const report = getValidResearchReport(prediction);
  if (!report) return null;

  return (
    <div className="mt-3 pt-3 border-t border-white/[0.06] space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <FileText size={13} className="text-brand-500" />
          <span className="uppercase tracking-wide font-semibold">AI Research Summary</span>
        </div>
      </div>
      <p className="text-[11px] text-gray-500 leading-relaxed">
        A structured summary of evidence already computed by StockSense360&rsquo;s engines — not a new recommendation.
      </p>

      {report.sections.map((section) => {
        const title = SECTION_TITLES[section.section_id] ?? section.title;
        if (ALWAYS_EXPANDED.has(section.section_id)) {
          return (
            <div key={section.section_id} className="space-y-1">
              <div className="text-[11px] font-semibold text-gray-500">{title}</div>
              <SectionBody section={section} />
            </div>
          );
        }
        return (
          <DisclosurePanel key={section.section_id} label={title}>
            <SectionBody section={section} />
          </DisclosurePanel>
        );
      })}
    </div>
  );
}
