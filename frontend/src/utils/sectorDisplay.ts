/**
 * Portfolio sector display normalization (Session 15).
 *
 * Root cause: Portfolio's sector comes from
 * stock_fundamentals_cache.sector_name, a direct passthrough of
 * screener.in's own peer-breadcrumb classification (see
 * services/fundamentals_cache.py). That breadcrumb is accurate but
 * sometimes too broad to be useful — screener.in files
 * JSLL / Jeena Sikho Lifecare under:
 *   Consumer Discretionary -> Consumer Services -> Leisure Services -> Wellness
 * "Consumer Services" is the raw `sector_name`; "Wellness" is the
 * `industry_name` one level down, and it's the more useful signal that
 * this is actually a healthcare/wellness business, not a generic
 * consumer-services one.
 *
 * This is a DISPLAY-layer fix only: the raw sector/industry values from
 * the cache are never modified (see fundamentals_cache.py's own docstring
 * on get_sectors_batch) — this function only decides what Portfolio's
 * allocation chart and holdings table show, from those two raw fields.
 */

// Substring match against `industry` (case-insensitive) — any of these
// present anywhere in the industry text means "this is a healthcare/
// wellness business," regardless of which broad sector screener.in filed
// it under. Deliberately generic (not a JSLL-specific hardcode) so any
// stock screener.in classifies this way gets the same treatment.
const HEALTHCARE_INDUSTRY_KEYWORDS = [
  "wellness",
  "hospital",
  "healthcare",
  "medical care",
  "diagnostic",
  "pharma", // catches both "Pharmaceuticals" and "Pharma"
];

/**
 * Returns the sector Portfolio should display for a holding, given the
 * raw `sector`/`industry` values from stock_fundamentals_cache. Never
 * mutates or discards the raw values — callers that need them (e.g. a
 * tooltip showing "Raw: Consumer Services / Wellness") should keep the
 * original `sector`/`industry` around separately.
 *
 * Returns `null` (not "Other") when there's nothing to go on — the same
 * "resolved, genuinely no sector" contract every existing Portfolio
 * sector-grouping call site already falls back to "Other" for.
 */
export function normalizeDisplaySector(
  sector: string | null | undefined,
  industry: string | null | undefined,
): string | null {
  const industryLower = (industry ?? "").trim().toLowerCase();
  if (industryLower && HEALTHCARE_INDUSTRY_KEYWORDS.some(kw => industryLower.includes(kw))) {
    return "Healthcare";
  }

  // Sector text already says Healthcare (or a close variant) on its own —
  // pass it through as-is rather than only trusting the industry keyword
  // list, so an already-correct classification is never second-guessed.
  const sectorTrimmed = (sector ?? "").trim();
  if (sectorTrimmed.toLowerCase().includes("healthcare")) return sectorTrimmed;

  return sectorTrimmed || null;
}
