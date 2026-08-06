// Sprint 3b — centralized factor-label registry for the Trade Postmortem
// explanation layers. Single source of truth mapping a governed
// (report_section, factor) pair — or, as fallback, a bare factor string —
// to a user-facing title. Every factor this codebase's rule registry can
// actually emit (backend/services/postmortem/evidence_attribution.py,
// price_path claim modules) MUST have an entry here; unknown/future
// factors fall back to a humanized (sentence-cased) version of the raw
// key rather than disappearing — see humanizeFactor() below, and the dev
// assertion in postmortemFactorLabels.test.ts.

export interface FactorLabelEntry {
  title: string;
  description?: string;
}

// Keyed by `${report_section}::${factor}`. Use this key form first;
// callers fall back to a section-agnostic lookup on `factor` alone (below)
// when no exact match exists, then to humanizeFactor().
const SECTION_FACTOR_LABELS: Record<string, FactorLabelEntry> = {
  // signal_scorecard — SIG_ENTRY_FACT_001
  "signal_scorecard::technical_signal": { title: "Technical Signal at Entry" },
  "signal_scorecard::recommendation_signal": { title: "Recommendation Signal at Entry" },
  "signal_scorecard::fundamental_score": { title: "Fundamental Score at Entry" },
  "signal_scorecard::sentiment_score": { title: "Sentiment Score at Entry" },

  // thesis_verdict — THESIS_DIRECTION_PATH_001
  "thesis_verdict::thesis_verdict": { title: "Entry Thesis vs. Price Path" },

  // contributor_assessments
  "contributor_assessments::POSITION_MANAGEMENT": { title: "Position Management" },
  "contributor_assessments::EXIT_LOGIC": { title: "Exit Logic" },
  "contributor_assessments::STOCK_SELECTION": { title: "Stock Selection" },
  "contributor_assessments::ENTRY_TIMING": { title: "Entry Timing" },
  "contributor_assessments::MARKET_CONDITIONS": { title: "Broad Market Conditions" },
  "contributor_assessments::SECTOR_CONDITIONS": { title: "Sector Conditions" },
  "contributor_assessments::VOLATILITY": { title: "Volatility Environment" },
  "contributor_assessments::LIQUIDITY": { title: "Liquidity Conditions" },
  "contributor_assessments::NEWS_OR_EVENT": { title: "News or Event Impact" },
  "contributor_assessments::PRICE_NOISE": { title: "Price Noise" },
  "contributor_assessments::ADMINISTRATIVE_ACTION": { title: "Administrative Action" },

  // contradictions
  "contradictions::entry_signal_agreement": { title: "Entry Signal Agreement" },
  "contradictions::momentum_vs_entry_range": { title: "Momentum vs. Entry Range" },
  "contradictions::favourable_entry_signal_vs_adverse_price_behavior": {
    title: "Favorable Entry Signal vs. Adverse Price Behavior",
  },
  "contradictions::favourable_stock_movement_vs_adverse_benchmark_movement": {
    title: "Favorable Stock Move vs. Adverse Benchmark Move",
  },
  "contradictions::positive_pnl_vs_unsupported_thesis": { title: "Positive P&L vs. Unsupported Thesis" },
  "contradictions::negative_pnl_vs_briefly_followed_thesis": { title: "Negative P&L vs. Briefly-Followed Thesis" },

  // primary_contributor
  "primary_contributor::primary_contributor": { title: "Primary Contributor to Outcome" },

  // price_path — factor names as surfaced in price-path claim rules
  "price_path::mfe": { title: "Maximum Favorable Excursion (MFE)" },
  "price_path::mae": { title: "Maximum Adverse Excursion (MAE)" },
  "price_path::target_touch": { title: "Target Touched During Holding Period" },
  "price_path::stop_touch": { title: "Stop Touched During Holding Period" },
  "price_path::touch_order": { title: "Order in Which Target/Stop Were Touched" },
};

// Section-agnostic fallback (factor alone), for cases where a factor
// appears in more than one section with the same meaning, or where the
// caller does not know the section.
const FACTOR_ONLY_LABELS: Record<string, FactorLabelEntry> = {
  technical_signal: { title: "Technical Signal at Entry" },
  recommendation_signal: { title: "Recommendation Signal at Entry" },
  fundamental_score: { title: "Fundamental Score at Entry" },
  sentiment_score: { title: "Sentiment Score at Entry" },
  thesis_verdict: { title: "Entry Thesis vs. Price Path" },
  POSITION_MANAGEMENT: { title: "Position Management" },
  EXIT_LOGIC: { title: "Exit Logic" },
  STOCK_SELECTION: { title: "Stock Selection" },
  ENTRY_TIMING: { title: "Entry Timing" },
  MARKET_CONDITIONS: { title: "Broad Market Conditions" },
  SECTOR_CONDITIONS: { title: "Sector Conditions" },
  VOLATILITY: { title: "Volatility Environment" },
  LIQUIDITY: { title: "Liquidity Conditions" },
  NEWS_OR_EVENT: { title: "News or Event Impact" },
  PRICE_NOISE: { title: "Price Noise" },
  ADMINISTRATIVE_ACTION: { title: "Administrative Action" },
  entry_signal_agreement: { title: "Entry Signal Agreement" },
  momentum_vs_entry_range: { title: "Momentum vs. Entry Range" },
  primary_contributor: { title: "Primary Contributor to Outcome" },
};

/** Sentence-case a raw snake/camel/CONST-case factor key as a safe fallback. */
export function humanizeFactor(factor: string): string {
  const spaced = factor
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .trim();
  if (!spaced) return factor;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export interface FactorLabelLookupResult {
  title: string;
  /** True when this came from a curated registry entry, false when humanized as a fallback. */
  curated: boolean;
}

export function getFactorLabel(reportSection: string, factor: string): FactorLabelLookupResult {
  const exact = SECTION_FACTOR_LABELS[`${reportSection}::${factor}`];
  if (exact) return { title: exact.title, curated: true };
  const bySelf = FACTOR_ONLY_LABELS[factor];
  if (bySelf) return { title: bySelf.title, curated: true };
  // Dev/test visibility: an uncurated factor still renders (never dropped),
  // but is flagged so it can be added to the registry above.
  if (process.env.NODE_ENV !== "production") {
    // eslint-disable-next-line no-console
    console.warn(
      `[postmortemFactorLabels] factor "${factor}" in section "${reportSection}" is missing a curated label — humanizing as fallback.`
    );
  }
  return { title: humanizeFactor(factor), curated: false };
}

export function getReportSectionLabel(section: string): string {
  const labels: Record<string, string> = {
    signal_scorecard: "Entry Signal Scorecard",
    thesis_verdict: "Entry Thesis Verdict",
    contributor_assessments: "Contributor Assessments",
    contradictions: "Contradictions Between Evidence",
    primary_contributor: "Primary Contributor",
    price_path: "Price Path",
  };
  return labels[section] ?? humanizeFactor(section);
}
