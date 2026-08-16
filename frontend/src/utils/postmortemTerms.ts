// Sprint 3b — centralized term-definition registry for the Trade
// Postmortem UI. Single source of truth for plain-English definitions of
// every governed status/enum/evidence-class term surfaced to the user.
export const POSTMORTEM_TERM_DEFINITIONS: Record<string, string> = {
  READY: "This report has finished generating and is complete.",
  PROCESSING: "This report is still being generated.",
  COMPLETE: "All evidence this report needed was available and verified.",
  LIMITED_EVIDENCE: "Some of the evidence this report needed was not available, so some conclusions could not be reached.",
  WIN: "The trade closed with a realized gain.",
  LOSS: "The trade closed with a realized loss.",
  BREAKEVEN: "The trade closed with a realized result close to zero.",
  INDETERMINATE: "The outcome of this trade could not be classified from the available data.",
  TARGET_HIT: "The trade was closed because its target price was reached (as reported at close).",
  STOP_LOSS: "The trade was closed because its stop-loss price was reached (as reported at close).",
  MANUAL: "The trade was closed manually rather than automatically by a target or stop.",
  SYSTEM_EXIT: "The trade was closed automatically by the system for a reason other than target or stop.",
  MFE: "Maximum Favorable Excursion — the best unrealized gain the position reached at any point while open.",
  MAE: "Maximum Adverse Excursion — the worst unrealized loss the position reached at any point while open.",
  "target touched": "Whether the price actually reached the target level at any point during the holding period, based on independently verified price history.",
  "stop touched": "Whether the price actually reached the stop level at any point during the holding period, based on independently verified price history.",
  "touch sequence": "The order in which the target and/or stop levels were reached, if both were reached.",
  "verified fact": "A claim that is mechanically derived from stored, governed data — not an interpretation.",
  "direct observation": "A claim based on a single directly-recorded data point, with no interpretation applied.",
  "supported interpretation": "A claim that draws a conclusion from available evidence, with a stated confidence level.",
  "conflicting evidence": "The available evidence points in different directions and could not be reconciled.",
  "insufficient evidence": "There is not enough evidence to reach a reliable conclusion for this factor.",
  "client-reported": "This value came from the client/session at the time of the trade, and has not been independently re-verified against an external price history.",
  "server-verified": "This value was independently verified by the server against an external, authoritative data source.",
  "point-in-time valid": "This evidence was captured at the moment it describes, not reconstructed afterward.",
  stale: "This evidence is older than the freshness window this report requires, and may no longer reflect current conditions.",
  "evidence gap": "A specific piece of evidence this report could not acquire, named explicitly rather than silently omitted.",
  warning: "A caution the report is raising about how to interpret its own output.",
  provenance: "The record of exactly which rule, evidence, and calculation version produced a given conclusion.",
  "evidence manifest": "A structured summary of which evidence categories were available when this report was generated.",
};

export function getTermDefinition(term: string): string | null {
  return POSTMORTEM_TERM_DEFINITIONS[term] ?? null;
}
