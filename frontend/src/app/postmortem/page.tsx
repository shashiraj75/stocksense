"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ChevronDown, ChevronUp, TrendingUp, TrendingDown, Minus, HelpCircle, Info,
} from "lucide-react";
import {
  fetchDailyPostmortem, type DailyTradePostmortem, type PostmortemClaim,
  type EvidenceItem, type Market,
} from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { useMarketPreference } from "@/hooks/useMarketPreference";
import { InsufficientEvidenceMark } from "@/components/InsufficientEvidenceMark";
import { isTradePostmortemDailyEnabled } from "@/utils/featureFlags";

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

// Trust-model color coding — a report section must visibly distinguish
// verified fact / direct observation / supported interpretation /
// conflicting evidence / insufficient evidence (Sprint 1, Stage 12).
// Deliberately NOT a confidence percentage anywhere — see ConfidenceBand's
// own comment in utils/api.ts.
const EVIDENCE_CLASS_STYLE: Record<string, string> = {
  MECHANICALLY_VERIFIED: "text-bull",
  DIRECTLY_OBSERVED: "text-gray-200",
  EVIDENCE_SUPPORTED: "text-brand-500",
  CONFLICTING_EVIDENCE: "text-neutral",
  INSUFFICIENT_EVIDENCE: "text-gray-500",
};

const EVIDENCE_CLASS_LABEL: Record<string, string> = {
  MECHANICALLY_VERIFIED: "Verified fact",
  DIRECTLY_OBSERVED: "Direct observation",
  EVIDENCE_SUPPORTED: "Supported interpretation",
  CONFLICTING_EVIDENCE: "Conflicting evidence",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
};

function OutcomeIcon({ outcome }: { outcome: string }) {
  if (outcome === "WIN") return <TrendingUp size={14} className="text-bull" />;
  if (outcome === "LOSS") return <TrendingDown size={14} className="text-bear" />;
  if (outcome === "BREAKEVEN") return <Minus size={14} className="text-gray-400" />;
  return <HelpCircle size={14} className="text-gray-500" />;
}

// The "Why this conclusion?" expandable area (Stage 12): rule ID/version,
// supporting/opposing evidence, missing evidence, limitations, confidence
// band — everything a caller would need to independently check the claim.
function WhyThisConclusion({ claim, evidenceById }: { claim: PostmortemClaim; evidenceById: Map<string, EvidenceItem> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-300"
      >
        <Info size={11} aria-hidden="true" />
        Why this conclusion?
      </button>
      {open && (
        <div className="mt-1.5 rounded-md border border-dark-border bg-black/20 p-2.5 text-[11px] space-y-1.5">
          <div className="text-gray-500">
            Rule <span className="text-gray-300">{claim.rule_id}</span> v{claim.rule_version} · confidence{" "}
            <span className="text-gray-300">{claim.confidence_band.replace(/_/g, " ")}</span>
          </div>
          {claim.supporting_evidence_ids.length > 0 && (
            <div>
              <span className="text-gray-500">Supporting: </span>
              {claim.supporting_evidence_ids.map((id) => {
                const ev = evidenceById.get(id);
                return <span key={id} className="text-gray-300 mr-2">{ev ? `${ev.name}=${String(ev.value)}` : id}</span>;
              })}
            </div>
          )}
          {claim.opposing_evidence_ids.length > 0 && (
            <div>
              <span className="text-gray-500">Opposing: </span>
              {claim.opposing_evidence_ids.map((id) => {
                const ev = evidenceById.get(id);
                return <span key={id} className="text-bear mr-2">{ev ? `${ev.name}=${String(ev.value)}` : id}</span>;
              })}
            </div>
          )}
          {claim.missing_evidence.length > 0 && (
            <div>
              <span className="text-gray-500">Missing: </span>
              <span className="text-gray-400">{claim.missing_evidence.join("; ")}</span>
            </div>
          )}
          {claim.limitations.length > 0 && (
            <div>
              <span className="text-gray-500">Limitations: </span>
              <span className="text-gray-400">{claim.limitations.join("; ")}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ClaimRow({ claim, evidenceById }: { claim: PostmortemClaim; evidenceById: Map<string, EvidenceItem> }) {
  return (
    <div className="py-1.5 border-b border-dark-border/50 last:border-0">
      <div className="flex items-start gap-1.5">
        <span className={clsx("text-[10px] font-semibold uppercase shrink-0 mt-0.5", EVIDENCE_CLASS_STYLE[claim.evidence_class])}>
          {EVIDENCE_CLASS_LABEL[claim.evidence_class]}
        </span>
        {claim.evidence_class === "INSUFFICIENT_EVIDENCE" && (
          <InsufficientEvidenceMark reason={claim.missing_evidence.join("; ") || claim.claim_text} />
        )}
      </div>
      <div className="text-xs text-gray-300 mt-0.5">{claim.claim_text}</div>
      <WhyThisConclusion claim={claim} evidenceById={evidenceById} />
    </div>
  );
}

function TradeCard({ trade }: { trade: DailyTradePostmortem }) {
  const [expanded, setExpanded] = useState(false);
  const pm = trade.postmortem;
  const attribution = trade.attribution;
  const pnl = pm.realized_pnl_abs;
  const evidenceById = new Map(attribution.evidence_items.map((e) => [e.evidence_id, e]));

  const primaryClaim = attribution.claims.find((c) => c.claim_id === attribution.primary_contributor_claim_id);
  const thesisClaim = attribution.claims.find((c) => c.claim_id === attribution.thesis_verdict_claim_id);
  const supportedContributors = attribution.contributor_assessments.filter(
    (c) => c.support_level === "STRONGLY_SUPPORTED" || c.support_level === "SUPPORTED"
  );

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

          {/* Signal scorecard — never derives a status from the trade's
              own P&L; see evidence_attribution.py's module docstring. */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Signal scorecard</div>
            <div className="space-y-1">
              {attribution.signal_scorecard.map((s) => (
                <div key={s.signal_id} className="flex items-center gap-1.5 text-xs">
                  <span className="capitalize text-gray-300">{s.signal_name}</span>
                  <span className="text-gray-500">{s.status.replace(/_/g, " ").toLowerCase()}</span>
                  {s.status === "INSUFFICIENT_EVIDENCE" && (
                    <InsufficientEvidenceMark reason={s.limitations.join("; ")} />
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Thesis verdict */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Thesis verdict</div>
            <div className="text-xs text-gray-300 mb-1">{attribution.thesis_verdict.replace(/_/g, " ")}</div>
            {thesisClaim && <ClaimRow claim={thesisClaim} evidenceById={evidenceById} />}
          </div>

          {/* Contributor assessments — zero, one, or many; never forced */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Contributor assessments</div>
            {supportedContributors.length === 0 ? (
              <div className="text-xs text-gray-500">
                No contributor category is supported by the available evidence for this trade.
              </div>
            ) : (
              <div className="space-y-1">
                {supportedContributors.map((c) => (
                  <div key={c.category} className="text-xs">
                    <span className="text-gray-300 capitalize">{c.category.replace(/_/g, " ").toLowerCase()}</span>
                    <span className="text-gray-500"> — {c.support_level.replace(/_/g, " ").toLowerCase()}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Primary contributor — nullable; the fallback sentence is
              shown, never a guessed single cause (Stage 9). */}
          <div>
            <div className="text-xs font-semibold text-gray-400 mb-1">Primary contributor</div>
            {attribution.primary_contributor ? (
              <div className="text-xs text-gray-300 capitalize">{attribution.primary_contributor.replace(/_/g, " ").toLowerCase()}</div>
            ) : (
              primaryClaim && <ClaimRow claim={primaryClaim} evidenceById={evidenceById} />
            )}
          </div>

          {/* Every remaining claim (entry-fact, contradiction, market-context
              placeholders, etc.) — full transparency, not a curated subset. */}
          <details className="text-xs">
            <summary className="text-gray-400 cursor-pointer">All claims ({attribution.claims.length})</summary>
            <div className="mt-1 max-h-96 overflow-y-auto">
              {attribution.claims.map((c) => <ClaimRow key={c.claim_id} claim={c} evidenceById={evidenceById} />)}
            </div>
          </details>
        </div>
      )}
    </div>
  );
}

export default function PostmortemPage() {
  const { user } = useAuth();
  const [date, setDate] = useState(todayISO());
  const [market, setMarket] = useMarketPreference<Market | "ALL">(["IN", "US", "ALL"], "ALL");

  // PR #32 pre-merge correction: dormant by default. `enabled` here is
  // TanStack Query's own fetch-gate — false means the query function
  // (fetchDailyPostmortem) is never invoked, so a disabled deployment
  // makes zero requests to the backend's own (also-gated) endpoint.
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["daily-postmortem", date, market, user?.id],
    queryFn: () => fetchDailyPostmortem(date, market),
    enabled: isTradePostmortemDailyEnabled() && !!user,
    staleTime: 60 * 1000,
  });

  if (!isTradePostmortemDailyEnabled()) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center">
        <h1 className="text-lg font-semibold text-gray-300">Postmortem reports aren&apos;t available yet</h1>
        <p className="text-sm text-gray-500 mt-2">This feature isn&apos;t enabled on this deployment yet.</p>
      </div>
    );
  }

  if (!user) {
    return <div className="max-w-4xl mx-auto px-4 py-8 text-gray-400">Sign in to view your Trade Postmortem Report.</div>;
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
      <div>
        <h1 className="text-xl font-bold">Daily Trade Postmortem Report</h1>
        <p className="text-sm text-gray-400 mt-1">
          Evidence-based analysis of every paper trade closed on the selected day. Every conclusion cites the
          evidence behind it; where evidence doesn&apos;t support a claim, this report says so explicitly rather
          than guessing.
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

          {Object.keys(data.summary.recurring_supported_contributors).length > 0 && (
            <div className="text-xs text-gray-400">
              Most frequently supported contributor(s):{" "}
              {Object.entries(data.summary.recurring_supported_contributors)
                .map(([k, v]) => `${k.replace(/_/g, " ").toLowerCase()} (${v})`)
                .join(", ")}
            </div>
          )}

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
