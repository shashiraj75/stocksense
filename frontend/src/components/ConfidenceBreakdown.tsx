import clsx from "clsx";
import { ConfidenceMeter } from "@/components/ConfidenceMeter";

export interface ConfidenceComponents {
  data_completeness: number;
  factor_agreement: number;
  earnings_stability: number;
  regime_certainty: number;
  historical_factor_reliability: number;
  _historical_reliability_live?: boolean;
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
  const displayKeys = (Object.keys(LABELS)) as (keyof typeof LABELS)[];

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
        {displayKeys.map((key) => {
          const isPendingHistorical = key === "historical_factor_reliability" && !isHistoricalLive;
          return (
            <div key={key} title={TOOLTIPS[key]}>
              {isPendingHistorical ? (
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
