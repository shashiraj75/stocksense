/**
 * Canonical prediction-horizon copy — single source of truth so the
 * landing page, Market Overview, Daily Picks, and Paper Trading never
 * drift into showing different day/week/month ranges for the same
 * horizon (found inconsistent across all four before this: "1–10 Days" /
 * "1–10 days" / "1–5 days" all describing "Short Term" in different
 * places, plus a landing-page-only "6M – 3 Years" Long Term range that
 * doesn't match the actual short 3–6 month horizon actually scored).
 *
 * "Core Investment" is intentionally NOT included here — it is a
 * future/planned horizon only, not a live signal. Any page that mentions
 * it must label it explicitly as future/planned and must not add it to
 * a live horizon tab, Daily Picks, or the Stock Detail API contract.
 */
export const HORIZON_INFO = [
  {
    key: "short",
    label: "Short Term",
    period: "1–5 trading days",
    desc: "Daily tactical/momentum call — technicals, volume, news sentiment.",
    color: "text-green-400",
    border: "border-green-500/40",
  },
  {
    key: "medium",
    label: "Medium Term",
    period: "2–4 weeks",
    desc: "Swing/position call, refreshed 2–3 times per week — earnings, sector rotation, macro trends.",
    color: "text-yellow-400",
    border: "border-yellow-500/40",
  },
  {
    key: "long",
    label: "Long Term",
    period: "3–6 months",
    desc: "Weekly call with stricter fundamental and risk review — management quality, growth.",
    color: "text-purple-400",
    border: "border-purple-500/40",
  },
] as const;

export type HorizonKey = (typeof HORIZON_INFO)[number]["key"];
