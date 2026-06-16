import clsx from "clsx";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";

export interface ConfidenceComponents {
  data_completeness: number;
  factor_agreement: number;
  earnings_stability: number;
  regime_certainty: number;
  historical_factor_reliability: number;
}

const LABELS: Record<keyof ConfidenceComponents, string> = {
  data_completeness: "Data Completeness",
  factor_agreement: "Factor Agreement",
  earnings_stability: "Earnings Stability",
  regime_certainty: "Regime Certainty",
  historical_factor_reliability: "Historical Reliability",
};

const BAND_CLASSES: Record<string, string> = {
  High: "bg-bull/20 text-bull border border-bull/40",
  Medium: "bg-yellow-500/20 text-yellow-400 border border-yellow-500/40",
  Low: "bg-bear/20 text-bear border border-bear/40",
};

export function ConfidenceBreakdown({
  score,
  band,
  components,
}: {
  score: number;
  band: string;
  components: ConfidenceComponents;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-gray-400 text-sm">Model Confidence</span>
        <span className="flex items-center gap-2">
          <span className="font-mono font-bold text-white">{score}</span>
          <span className={clsx("text-xs font-bold tracking-wide rounded-full px-2 py-0.5", BAND_CLASSES[band])}>
            {band}
          </span>
        </span>
      </div>
      <div className="space-y-2">
        {(Object.keys(LABELS) as (keyof ConfidenceComponents)[]).map((key) => (
          <ConfidenceMeter key={key} value={components[key]} label={LABELS[key]} />
        ))}
      </div>
    </div>
  );
}
