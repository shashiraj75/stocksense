// Sprint 3b — deterministic Layer-1 executive-summary builder for the
// Trade Postmortem page. Built ONLY from governed report fields already on
// CurrentReportReadResponse (status, availability, outcome, exit_mechanism,
// realized pnl, schema_version). No per-trade hardcoding, no fresh
// inference beyond simple, deterministic sentence composition.
import type { CurrentReportReadResponse, JSONValue } from "@/utils/api";

function asStr(v: JSONValue | undefined): string | null {
  return typeof v === "string" ? v : null;
}
function asNum(v: JSONValue | undefined): number | null {
  return typeof v === "number" ? v : null;
}

export interface Layer1Fields {
  outcome: string | null;
  realizedPnlAbs: number | null;
  realizedPnlPct: number | null;
  exitMechanism: string | null;
  status: CurrentReportReadResponse["status"];
  schemaVersion: string | null;
}

export function extractLayer1Fields(report: CurrentReportReadResponse): Layer1Fields {
  const postmortem = report.structured_report?.postmortem;
  const obj = typeof postmortem === "object" && postmortem !== null ? (postmortem as Record<string, JSONValue>) : null;
  return {
    outcome: obj ? asStr(obj.outcome) : null,
    realizedPnlAbs: obj ? asNum(obj.realized_pnl_abs) : null,
    realizedPnlPct: obj ? asNum(obj.realized_pnl_pct) : null,
    exitMechanism: obj ? asStr(obj.exit_mechanism) : null,
    status: report.status,
    schemaVersion: report.report_schema_version,
  };
}

function outcomeSentence(fields: Layer1Fields): string {
  const { outcome, realizedPnlAbs, realizedPnlPct } = fields;
  const pnlPhrase =
    realizedPnlAbs !== null && realizedPnlPct !== null
      ? ` (${realizedPnlAbs >= 0 ? "+" : ""}${realizedPnlAbs.toFixed(2)}, ${realizedPnlPct >= 0 ? "+" : ""}${realizedPnlPct.toFixed(2)}%)`
      : "";
  switch (outcome) {
    case "WIN":
      return `This trade closed as a win${pnlPhrase}.`;
    case "LOSS":
      return `This trade closed as a loss${pnlPhrase}.`;
    case "BREAKEVEN":
      return `This trade closed roughly breakeven${pnlPhrase}.`;
    case "INDETERMINATE":
      return "This trade's outcome could not be classified from the available data.";
    default:
      return "This trade's outcome is not recorded on this report.";
  }
}

function exitSentence(exitMechanism: string | null): string {
  switch (exitMechanism) {
    case "TARGET_HIT":
      return "It was closed because its reported target price was reached.";
    case "STOP_LOSS":
      return "It was closed because its reported stop-loss price was reached.";
    case "MANUAL":
      return "It was closed manually.";
    case "SYSTEM_EXIT":
      return "It was closed automatically by the system for a reason other than the target or stop.";
    default:
      return "";
  }
}

function evidenceSentence(fields: Layer1Fields): string {
  if (fields.status === "COMPLETE") {
    return "All the evidence this report needed was available, so its conclusions are fully supported.";
  }
  if (fields.status === "LIMITED_EVIDENCE") {
    return "Some of the evidence this report needed was not available, so some of its conclusions could not be reached — this is explained factor by factor below.";
  }
  return "This report's evidence completeness is not recorded.";
}

/**
 * Builds the single, deterministic Layer-1 paragraph. No branching on
 * trade identity, symbol, or any field outside the governed set above —
 * the same function produces the same shape of sentence for any trade
 * with the same governed field values.
 */
export function buildLayer1Summary(report: CurrentReportReadResponse): string {
  const fields = extractLayer1Fields(report);
  const parts = [outcomeSentence(fields), exitSentence(fields.exitMechanism), evidenceSentence(fields)].filter(
    (s) => s.length > 0
  );
  return parts.join(" ");
}
