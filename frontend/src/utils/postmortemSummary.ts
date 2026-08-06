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

// Single-source-of-truth currency formatter — shared by the executive
// summary paragraph AND the price-path summary cards in page.tsx (do not
// duplicate this in more than one place; a prior duplicate copy in
// page.tsx produced "+1896.45" for an India report instead of
// "+₹1,896.45" — no currency symbol, no thousands grouping).
//
// Currency is derived from the persisted report's own `market`, never
// guessed or defaulted — an unrecognized future market still shows the
// number, just without inventing a currency symbol for it.
export function currencySymbolFor(market: string | null): string | null {
  if (market === "IN") return "₹";
  if (market === "US") return "$";
  return null;
}

export function fmtPct(v: number | null): string {
  return v === null ? "N/A" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** Never applied to percentages — fmtPct above has no currency parameter at all. */
export function fmtAbs(v: number | null, currencySymbol: string | null): string {
  if (v === null) return "N/A";
  const sign = v >= 0 ? "+" : "";
  const magnitude = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${sign}${v < 0 ? "-" : ""}${currencySymbol ?? ""}${magnitude}`;
}

export interface Layer1Fields {
  outcome: string | null;
  realizedPnlAbs: number | null;
  realizedPnlPct: number | null;
  exitMechanism: string | null;
  status: CurrentReportReadResponse["status"];
  schemaVersion: string | null;
  market: string | null;
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
    market: report.market ?? null,
  };
}

function outcomeSentence(fields: Layer1Fields): string {
  const { outcome, realizedPnlAbs, realizedPnlPct, market } = fields;
  const currencySymbol = currencySymbolFor(market);
  const pnlPhrase =
    realizedPnlAbs !== null && realizedPnlPct !== null
      ? ` (${fmtAbs(realizedPnlAbs, currencySymbol)}, ${fmtPct(realizedPnlPct)})`
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
