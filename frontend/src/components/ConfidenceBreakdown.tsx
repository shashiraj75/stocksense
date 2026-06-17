import clsx from "clsx";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";

export interface ConfidenceComponents {
  data_completeness: number;
  factor_agreement: number;
  earnings_stability: number;
  regime_certainty: number;
  historical_factor_reliability: number;
  _historical_reliability_live?: boolean;
  _earnings_stability_live?: boolean;
}

const LABELS: Record<string, string> = {
  data_completeness:            "Data Completeness",
  factor_agreement:             "Factor Agreement",
  earnings_stability:           "Earnings Stability",
  regime_certainty:             "Regime Certainty",
  historical_factor_reliability: "Historical Reliability",
};

const TOOLTIPS: Record<string, string> = {
  data_completeness:            "% of key fundamental fields available (PE, ROE, ROCE, growth, margins…)",
  factor_agreement:             "% of scoring factors (tech, fundamentals, sentiment, quality) pointing in the same direction as the signal",
  earnings_stability:           "EPS surprise trend + analyst upgrade/downgrade momentum over the last 4 quarters",
  regime_certainty:             "Strength of the broad market trend — bull/bear trending markets produce more reliable signals than sideways markets",
  historical_factor_reliability: "How reliably each factor has predicted actual returns (IC score). Requires 60+ resolved predictions to activate.",
};

const BAND_CLASSES: Record<string, string> = {
  High:   "bg-bull/20 text-bull border border-bull/40",
  Medium: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40",
  Low:    "bg-bear/20 text-bear border border-bear/40",
};

// Convert score to a human-readable percentile context
// Based on empirical distribution of confidence scores across Nifty 100 predictions
function getPercentileLabel(score: number): { label: string; sub: string } {
  if (score >= 80) return { label: "Top 10%",  sub: "of all Nifty predictions" };
  if (score >= 72) return { label: "Top 20%",  sub: "of all Nifty predictions" };
  if (score >= 65) return { label: "Top 35%",  sub: "of all Nifty predictions" };
  if (score >= 58) return { label: "Top 50%",  sub: "of all Nifty predictions" };
  if (score >= 50) return { label: "Below avg", sub: "weaker signal clarity" };
  return               { label: "Low range",  sub: "limited data or mixed signals" };
}

// SVG dial gauge for the confidence score
function ConfidenceGauge({ score, band }: { score: number; band: string }) {
  const clampedScore = Math.max(0, Math.min(100, score));
  // Arc goes from 210° to -30° (240° sweep) — left to right
  const startAngle = 210;
  const sweepAngle = 240;
  const angle = startAngle - (clampedScore / 100) * sweepAngle;
  const rad = (a: number) => (a * Math.PI) / 180;
  const cx = 60; const cy = 58; const r = 42;
  // Needle endpoint
  const nx = cx + r * Math.cos(rad(angle));
  const ny = cy - r * Math.sin(rad(angle));
  const trackColor = "#1f2937";
  const fillColor = band === "High" ? "#22c55e" : band === "Medium" ? "#f59e0b" : "#ef4444";

  // Arc path helper
  const arcPath = (startDeg: number, endDeg: number, radius: number) => {
    const s = { x: cx + radius * Math.cos(rad(startDeg)), y: cy - radius * Math.sin(rad(startDeg)) };
    const e = { x: cx + radius * Math.cos(rad(endDeg)),   y: cy - radius * Math.sin(rad(endDeg)) };
    const large = Math.abs(startDeg - endDeg) > 180 ? 1 : 0;
    return `M ${s.x} ${s.y} A ${radius} ${radius} 0 ${large} 0 ${e.x} ${e.y}`;
  };

  const fillEnd = startAngle - (clampedScore / 100) * sweepAngle;

  return (
    <svg viewBox="0 0 120 80" width="120" height="80" aria-hidden="true">
      {/* Track */}
      <path d={arcPath(startAngle - sweepAngle, startAngle, r)} fill="none" stroke={trackColor} strokeWidth="7" strokeLinecap="round" />
      {/* Fill */}
      <path d={arcPath(fillEnd, startAngle, r)} fill="none" stroke={fillColor} strokeWidth="7" strokeLinecap="round" />
      {/* Needle */}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={fillColor} strokeWidth="2" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="3.5" fill={fillColor} />
      {/* Score label */}
      <text x={cx} y={cy + 18} textAnchor="middle" fill="white" fontSize="14" fontWeight="700" fontFamily="monospace">{score}</text>
    </svg>
  );
}

export function ConfidenceBreakdown({
  score,
  band,
  components,
}: {
  score: number;
  band: string;
  components: ConfidenceComponents;
}) {
  const isHistoricalLive = components._historical_reliability_live === true;
  const isEarningsLive = components._earnings_stability_live === true;
  const displayKeys = (Object.keys(LABELS)) as (keyof typeof LABELS)[];
  const { label: pctLabel, sub: pctSub } = getPercentileLabel(score);

  return (
    <div className="space-y-3">
      {/* Score row — gauge + number + band + percentile */}
      <div className="flex items-center gap-4">
        <ConfidenceGauge score={score} band={band} />
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-gray-400 text-sm">Model Confidence</span>
            <span className={clsx("text-xs font-bold tracking-wide rounded-full px-2 py-0.5", BAND_CLASSES[band])}>
              {band}
            </span>
          </div>
          {/* Percentile context — the key confidence builder */}
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-white">{pctLabel}</span>
            <span className="text-xs text-gray-500">{pctSub}</span>
          </div>
        </div>
      </div>

      {/* Component bars */}
      <div className="space-y-2">
        {displayKeys.map((key) => {
          const isPendingHistorical = key === "historical_factor_reliability" && !isHistoricalLive;
          const isPendingEarnings = key === "earnings_stability" && !isEarningsLive;
          const isPending = isPendingHistorical || isPendingEarnings;
          return (
            <div key={key} title={TOOLTIPS[key]}>
              {isPending ? (
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-gray-400">{LABELS[key]}</p>
                    <span className="text-xs font-mono text-gray-500 italic">Pending data</span>
                  </div>
                  <div className="h-2 bg-dark-border rounded-full overflow-hidden">
                    <div className="h-full w-0 rounded-full bg-gray-600" />
                  </div>
                </div>
              ) : (
                <ConfidenceMeter value={(components as any)[key]} label={LABELS[key]} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
