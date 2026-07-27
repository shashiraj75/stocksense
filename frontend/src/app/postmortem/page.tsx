"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ChevronDown, ChevronUp, TrendingUp, TrendingDown, Minus, HelpCircle,
} from "lucide-react";
import { fetchDailyPostmortem, type DailyTradePostmortem, type SignalFactorAssessment, type Market } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { InsufficientEvidenceMark } from "@/components/InsufficientEvidenceMark";

const MARKETS: { key: Market | "ALL"; label: string }[] = [
  { key: "ALL", label: "All Markets" },
  { key: "IN", label: "🇮🇳 India" },
  { key: "US", label: "🇺🇸 US" },
];

function todayISO(): string {
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

const OUTCOME_STYLE: Record<string, string> = {
  WIN: "text-bull",
  LOSS: "text-bear",
  BREAKEVEN: "text-gray-400",
  INDETERMINATE: "text-gray-500",
};

function OutcomeIcon({ outcome }: { outcome: string }) {
  if (outcome === "WIN") return <TrendingUp size={14} className="text-bull" />;
  if (outcome === "LOSS") return <TrendingDown size={14} className="text-bear" />;
  if (outcome === "BREAKEVEN") return <Minus size={14} className="text-gray-400" />;
  return <HelpCircle size={14} className="text-gray-500" />;
}

function SignalRow({ assessment }: { assessment: SignalFactorAssessment }) {
  const label = assessment.factor.replace(/_/g, " ");
  if (assessment.agreement === "INSUFFICIENT_EVIDENCE") {
    return (
      <div className="flex items-center gap-1.5 text-xs text-gray-500">
        <span className="capitalize">{label}</span>
        <InsufficientEvidenceMark reason={assessment.reason} />
      </div>
    );
  }
  const agreed = assessment.agreement === "AGREED";
  return (
    <div className="flex items-center gap-1.5 text-xs" title={assessment.reason}>
      <span className="capitalize text-gray-300">{label}</span>
      <span className={agreed ? "text-bull" : "text-bear"}>{agreed ? "Agreed" : "Contradicted"}</span>
    </div>
  );
}

function TradeCard({ trade }: { trade: DailyTradePostmortem }) {
  const [expanded, setExpanded] = useState(false);
  const pm = trade.postmortem;
  const narrative = trade.narrative;
  const pnl = pm.realized_pnl_abs;

  return (
    <div className="rounded-lg border border-dark-border bg-dark-card overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-white/5"
      >
        <div className="flex items-center gap-3 min-w-0">
          <OutcomeIcon outcome={pm.outcome} />
          <span className="font-semibold truncate">{trade.symbol}</span>
          <span className="text-xs text-gray-500">{trade.market}</span>
          <span className={clsx("text-xs font-medium", OUTCOME_STYLE[pm.outcome])}>{pm.outcome}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {pnl !== null ? (
            <span className={clsx("text-sm font-semibold tabular-nums", pnl >= 0 ? "text-bull" : "text-bear")}>
              {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
            </span>
          ) : (
            <span className="text-sm text-gray-500">N/A</span>
          )}
          {expanded ? <ChevronUp size={16} className="text-gray-400" /> : <ChevronDown size={16} className="text-gray-400" />}
        </div>
      </button>

      {expanded && (
        <div className="border-t border-dark-border px-4 py-3 space-y-4 text-sm">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <div>
              <div className="text-gray-500">P&amp;L %</div>
              <div className="font-medium">{pm.realized_pnl_pct !== null ? `${pm.realized_pnl_pct.toFixed(2)}%` : "N/A"}</div>
            </div>
            <div>
              <div className="text-gray-500">Held</div>
              <div className="font-medium">
                {pm.holding_duration_seconds !== null
                  ? `${(pm.holding_duration_seconds / 3600).toFixed(1)}h`
                  : "N/A"}
              </div>
            </div>
            <div>
              <div className="text-gray-500">Exit mechanism</div>
              <div className="font-medium">{pm.exit_mechanism}</div>
            </div>
            <div>
              <div className="text-gray-500">Evidence</div>
              <div className="font-medium">{pm.evidence_completeness}</div>
            </div>
          </div>

          {/* Q4: entry conditions */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Entry conditions</div>
            {narrative.entry_conditions.length > 0 ? (
              <ul className="text-xs text-gray-300 space-y-0.5 list-disc list-inside">
                {narrative.entry_conditions.map((fact, i) => <li key={i}>{fact}</li>)}
              </ul>
            ) : (
              <div className="flex items-center gap-1.5 text-xs text-gray-500">
                <span>No entry evidence recorded</span>
                {narrative.entry_conditions_reason && <InsufficientEvidenceMark reason={narrative.entry_conditions_reason} />}
              </div>
            )}
          </div>

          {/* Q5/6/7: signal effectiveness + price-move cause */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Signal effectiveness</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {narrative.signal_factors.map((s) => <SignalRow key={s.factor} assessment={s} />)}
            </div>
          </div>

          {/* Q8: market context */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Market &amp; timing context</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(narrative.market_context).map(([key, assessment]) => (
                <SignalRow key={key} assessment={assessment as SignalFactorAssessment} />
              ))}
            </div>
          </div>

          {/* Q10/11/12: thesis, root cause, takeaway */}
          <div className="space-y-1.5">
            <div className="text-xs">
              <span className="text-gray-400">Thesis: </span>
              <span className="font-medium">{narrative.thesis_assessment.replace(/_/g, " ")}</span>
              <span className="text-gray-500"> — {narrative.thesis_reason}</span>
            </div>
            <div className="text-xs">
              <span className="text-gray-400">Root cause: </span>
              <span className="font-medium">{narrative.root_cause.replace(/_/g, " ")}</span>
              <span className="text-gray-500"> — {narrative.root_cause_reason}</span>
            </div>
            <div className="text-xs italic text-gray-300 border-l-2 border-brand-500/50 pl-2">
              {narrative.takeaway}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function PostmortemPage() {
  const { user } = useAuth();
  const [date, setDate] = useState(todayISO());
  const [market, setMarket] = useMarketPreference<Market | "ALL">(["IN", "US", "ALL"], "ALL");

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["daily-postmortem", date, market, user?.id],
    queryFn: () => fetchDailyPostmortem(date, market),
    enabled: !!user,
    staleTime: 60 * 1000,
  });

  if (!user) {
    return <div className="max-w-4xl mx-auto px-4 py-8 text-gray-400">Sign in to view your Trade Postmortem Report.</div>;
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
      <div>
        <h1 className="text-xl font-bold">Daily Trade Postmortem Report</h1>
        <p className="text-sm text-gray-400 mt-1">
          Deterministic, evidence-based analysis of every paper trade closed on the selected day. Where evidence
          doesn&apos;t support a claim, this report says so explicitly rather than guessing.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <input
          type="date"
          value={date}
          max={todayISO()}
          onChange={(e) => setDate(e.target.value)}
          className="bg-dark-card border border-dark-border rounded-lg px-3 py-1.5 text-sm"
        />
        <div className="flex items-center gap-1 rounded-lg border border-dark-border p-0.5">
          {MARKETS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMarket(m.key)}
              className={clsx(
                "px-2.5 py-1 rounded-md text-xs font-medium",
                market === m.key ? "bg-white/10 text-white" : "text-gray-400 hover:text-white"
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {isLoading && <div className="text-sm text-gray-400 py-8 text-center">Loading…</div>}
      {isError && (
        <div className="text-sm text-bear py-8 text-center">
          Could not load the report{error instanceof Error ? `: ${error.message}` : "."}
        </div>
      )}

      {data && (
        <>
          <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-3 grid grid-cols-2 sm:grid-cols-5 gap-3 text-xs">
            <div>
              <div className="text-gray-500">Trades</div>
              <div className="font-semibold text-sm">{data.summary.trade_count}</div>
            </div>
            <div>
              <div className="text-gray-500">Wins / Losses</div>
              <div className="font-semibold text-sm">
                <span className="text-bull">{data.summary.win_count}</span>
                {" / "}
                <span className="text-bear">{data.summary.loss_count}</span>
              </div>
            </div>
            <div>
              <div className="text-gray-500">Net P&amp;L</div>
              <div className="font-semibold text-sm">
                {data.summary.total_realized_pnl_abs !== null
                  ? `${data.summary.total_realized_pnl_abs >= 0 ? "+" : ""}${data.summary.total_realized_pnl_abs.toFixed(2)}`
                  : "N/A"}
                {data.summary.pnl_excluded_trade_count > 0 && (
                  <span className="text-gray-500 font-normal">
                    {" "}({data.summary.pnl_excluded_trade_count} excluded)
                  </span>
                )}
              </div>
            </div>
            <div>
              <div className="text-gray-500">Breakeven</div>
              <div className="font-semibold text-sm">{data.summary.breakeven_count}</div>
            </div>
            <div>
              <div className="text-gray-500">Indeterminate</div>
              <div className="font-semibold text-sm">{data.summary.indeterminate_count}</div>
            </div>
          </div>

          {data.trades.length === 0 ? (
            <div className="text-sm text-gray-500 py-10 text-center">No closed trades on this day.</div>
          ) : (
            <div className="space-y-2">
              {data.trades.map((t) => <TradeCard key={t.trade_id} trade={t} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
