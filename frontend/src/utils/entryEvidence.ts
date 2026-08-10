// Trade Postmortem Evidence Completion, Phase A1 — deterministic mapping
// from an already-loaded `Prediction` (whatever the caller's own useQuery
// already fetched — no network I/O happens here) to the
// `EntryEvidencePayload` the backend accepts on Buy.
//
// Governing rule (see backend/services/postmortem/entry_snapshot.py and the
// Phase A1 spec): capture only what StockSense360 ACTUALLY KNEW at entry.
// Never fabricate a default for a missing field — NULL/omitted is always
// preferable to a guess. A genuine numeric `0` (e.g. sentiment_score of 0,
// technical_rsi of 0) is real evidence and must NOT be dropped by a
// truthiness check — every numeric field below is checked with
// `Number.isFinite` (which also naturally excludes NaN and ±Infinity, so
// nothing malformed is ever submitted).
import type { EntryEvidencePayload, Prediction } from "./api";

// Only forwards a finite number; drops NaN/Infinity/undefined/null without
// ever substituting a fabricated 0.
function finiteOrNull(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringOrNull(value: string | null | undefined): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Builds the entry-evidence payload for a RESEARCH-path Buy (Stock Detail)
 * from a live `Prediction` object. Pure function — never mutates
 * `prediction`, never invents a timestamp, model_version, or score.
 *
 * Callers are responsible for passing the prediction that corresponds to
 * the FINAL horizon actually selected at Buy time (never a stale
 * previously-loaded horizon's prediction) — this helper has no opinion on
 * horizon selection, it only maps whatever is handed to it.
 */
export function buildEntryEvidenceFromPrediction(
  prediction: Prediction | null | undefined
): EntryEvidencePayload | null {
  if (!prediction) return null;

  const levels = prediction.trade_levels;

  const payload: EntryEvidencePayload = {
    recommendation_signal: stringOrNull(prediction.signal),
    recommendation_generated_at: stringOrNull(prediction.generated_at ?? null),
    recommendation_reference_price: finiteOrNull(prediction.current_price),
    recommendation_entry_low: finiteOrNull(levels?.entry_low),
    recommendation_entry_high: finiteOrNull(levels?.entry_high),
    recommended_stop_loss: finiteOrNull(levels?.stop_loss),
    recommended_target_price: finiteOrNull(levels?.take_profit),
    confidence_score: finiteOrNull(prediction.confidence),
    technical_signal: stringOrNull(prediction.technical?.overall ?? null),
    technical_rsi: finiteOrNull(prediction.technical?.rsi),
    technical_macd_diff: finiteOrNull(prediction.technical?.macd_diff),
    fundamental_score: finiteOrNull(prediction.fundamental_score?.score),
    sentiment_score: finiteOrNull(prediction.sentiment_score?.score),
    sentiment_label: stringOrNull(prediction.sentiment_score?.label ?? null),
    market_regime_trend: stringOrNull(prediction.market_regime?.trend ?? null),
    market_regime_score_adj: finiteOrNull(prediction.market_regime?.score_adj),
    market_regime_reason: stringOrNull(prediction.market_regime?.reason ?? null),
    recommendation_reasoning:
      Array.isArray(prediction.reasoning) && prediction.reasoning.length > 0
        ? prediction.reasoning
        : null,
    // daily_pick_run_id / daily_pick_rank / model_version are never
    // populated here — a live Prediction response carries none of these
    // with governed semantics for a RESEARCH-path Buy. Callers with a
    // genuine Daily Pick payload build a separate payload (see
    // buildEntryEvidenceFromDailyPick below), not this function.
    daily_pick_run_id: null,
    daily_pick_rank: null,
    model_version: null,
  };

  return payload;
}

// Subset of the Daily Picks `Pick` shape this helper actually reads.
// Intentionally narrow — only fields Phase A1 needs and the live
// /api/picks response genuinely returns today.
export interface DailyPickEvidenceSource {
  price: number | null | undefined;
  entry_low?: number | null;
  entry_high?: number | null;
  stop_loss?: number | null;
  target?: number | null;
  confidence?: number | null;
  tech_score?: number | null;
  fund_score?: number | null;
  sentiment?: string | null;
  // Phase A1 evidence-gap closure: the governed technical-signal vocabulary
  // (BUY/SELL/HOLD) and the genuine numeric sentiment score, both additive
  // fields the Daily Picks backend now emits from the exact same
  // PredictionEngine.predict() computation the Research/Stock Detail path
  // already exposes via `technical.overall` / `sentiment_score.score`. Null
  // when the backend genuinely didn't produce them (e.g. legacy cached
  // picks generated before this field existed) — never fabricated here.
  technical_signal?: string | null;
  sentiment_score?: number | null;
  reasoning?: { indicator: string; signal: string; reason: string }[] | null;
  generated_at?: string | null;
  horizon?: string | null;
}

/**
 * Builds the entry-evidence payload for a DAILY_PICK-path Buy directly from
 * the Daily Pick object the user actually saw on the Picks page — never
 * from a fresh /api/predictions call, which would describe a different
 * (possibly since-changed) recommendation than the one the user acted on.
 *
 * daily_pick_run_id / daily_pick_rank are intentionally NOT set here: the
 * live Daily Picks payload does not carry a governed run identifier or a
 * rank field distinct from on-page list position, so populating either
 * would mean inferring rather than capturing real evidence.
 */
export function buildEntryEvidenceFromDailyPick(
  pick: DailyPickEvidenceSource | null | undefined
): EntryEvidencePayload | null {
  if (!pick) return null;

  return {
    recommendation_signal: "BUY",
    recommendation_generated_at: stringOrNull(pick.generated_at ?? null),
    recommendation_reference_price: finiteOrNull(pick.price ?? null),
    recommendation_entry_low: finiteOrNull(pick.entry_low),
    recommendation_entry_high: finiteOrNull(pick.entry_high),
    recommended_stop_loss: finiteOrNull(pick.stop_loss),
    recommended_target_price: finiteOrNull(pick.target),
    confidence_score: finiteOrNull(pick.confidence),
    // Phase A1 evidence-gap closure: consume ONLY the newly authoritative
    // `technical_signal` / `sentiment_score` fields the backend now emits
    // directly from the Daily Pick's own generation computation — never
    // derived from `tech_score` (threshold conversion) or from the
    // `sentiment` label (label-to-number mapping). Stays null exactly when
    // the backend didn't genuinely produce a value.
    technical_signal: stringOrNull(pick.technical_signal ?? null),
    technical_rsi: null,
    technical_macd_diff: null,
    fundamental_score: finiteOrNull(pick.fund_score),
    sentiment_score: finiteOrNull(pick.sentiment_score),
    sentiment_label: stringOrNull(pick.sentiment ?? null),
    market_regime_trend: null,
    market_regime_score_adj: null,
    market_regime_reason: null,
    recommendation_reasoning:
      Array.isArray(pick.reasoning) && pick.reasoning.length > 0 ? pick.reasoning : null,
    daily_pick_run_id: null,
    daily_pick_rank: null,
    model_version: null,
  };
}
