"use client";
import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import clsx from "clsx";
import { Info } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCurrentPostmortemReport, type CurrentReportReadResponse, type JSONValue,
  type PostmortemClaim, type EvidenceItem,
} from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import { InsufficientEvidenceMark } from "@/components/InsufficientEvidenceMark";
import { isTradePostmortemPricePathEnabled } from "@/utils/featureFlags";
import { buildLayer1Summary, fmtAbs, fmtPct, currencySymbolFor } from "@/utils/postmortemSummary";
import { ClaimExplanationLayer } from "@/components/postmortem/ClaimExplanationLayer";
import {
  isUnverifiedTargetHitCloseCase, UNVERIFIED_TARGET_HIT_EXPLANATION, resolveAvailabilityReason,
} from "@/utils/postmortemAvailabilityReason";

// ============================================================================
// Wave C, WC-N — /postmortem/[tradeId]: the per-trade governed current-report
// page. Reuses the exact evidence-class styling/labels and "Why this
// conclusion?" pattern established by the Sprint 1 daily report
// (src/app/postmortem/page.tsx) rather than inventing a second vocabulary.
// ============================================================================

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

// Wave C, WC-N — bounded PROCESSING polling. `structured_report.price_path`'s
// non-version_and_provenance keys (mfe/mae/touch fields below) are
// genuinely untyped JSONValue on the frozen backend contract (extra="allow"
// containers) — these guards read them defensively rather than assuming a
// shape the contract doesn't guarantee.
const POLL_INTERVAL_MS = 4000;
// 30 attempts * 4s = ~120s bounded window — generation is a short
// synchronous best-effort attempt plus limited background retries; if a
// report still isn't READY after two minutes, this is a genuinely
// exceptional case the user should be told about explicitly, not one this
// page silently keeps polling forever.
const MAX_POLL_ATTEMPTS = 30;

function asNum(v: JSONValue | undefined): number | null {
  return typeof v === "number" ? v : null;
}
function asStr(v: JSONValue | undefined): string | null {
  return typeof v === "string" ? v : null;
}
function asBool(v: JSONValue | undefined): boolean | null {
  return typeof v === "boolean" ? v : null;
}

// Bounded, safe rendering for an evidence item's `value` — never
// "[object Object]" for an array/object, never dangerouslySetInnerHTML
// (JSON.stringify output is rendered as plain text, which React already
// escapes), and never an unbounded dump of a large nested structure.
const MAX_EVIDENCE_VALUE_LENGTH = 200;
function safeFormatValue(value: JSONValue): string {
  if (value === null) return "null";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  let json: string;
  try {
    json = JSON.stringify(value) ?? "null";
  } catch {
    return "(unrepresentable value)";
  }
  return json.length > MAX_EVIDENCE_VALUE_LENGTH ? `${json.slice(0, MAX_EVIDENCE_VALUE_LENGTH)}…` : json;
}

// The Closure classification summary field is sourced ONLY from the
// governed EXIT/closure_classification evidence item — never inferred
// from P&L sign, exit_mechanism, exit_reason, touch evidence, or claim
// text, all of which can disagree with the trade's actual governed
// closure classification (e.g. a manual sell above target is not a
// genuine target-hit).
function findClosureClassification(evidenceItems: EvidenceItem[]): string | null {
  const item = evidenceItems.find((e) => e.category === "EXIT" && e.name === "closure_classification");
  return item && typeof item.value === "string" ? item.value : null;
}

function useBoundedProcessingPoll(tradeId: number, enabled: boolean) {
  const [pollCount, setPollCount] = useState(0);

  // Reset the bounded window whenever the trade being viewed changes —
  // never carry a stale attempt count (or a stale timed-out state) from a
  // previously-viewed trade onto a newly-navigated one.
  useEffect(() => {
    setPollCount(0);
  }, [tradeId]);

  const query = useQuery({
    queryKey: ["current-report", tradeId],
    queryFn: ({ signal }) => fetchCurrentPostmortemReport(tradeId, signal),
    enabled,
    // Never turn an auth/ownership/not-found failure into an infinite
    // retry loop — a 401/403/404 is a stable outcome the UI must show
    // immediately, not mask behind repeated silent retries.
    retry: false,
    // A focus/reconnect-triggered refetch would otherwise let the user
    // bypass the bounded polling window (e.g. re-focusing the tab after
    // MAX_POLL_ATTEMPTS is reached) — polling is governed exclusively by
    // refetchInterval below, never by these implicit triggers.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (q) => {
      // q.state.data can retain the PREVIOUS successful (PROCESSING)
      // response even after a subsequent request has failed — q.state.data
      // is not cleared on error. Checking q.state.status first is what
      // actually stops polling after a failed interval request (401, 403,
      // 404, or a network failure); checking .data alone would silently
      // keep scheduling further requests forever using stale data.
      if (q.state.status === "error") return false;
      const availability = (q.state.data as CurrentReportReadResponse | undefined)?.availability;
      if (availability !== "PROCESSING") return false;
      if (pollCount >= MAX_POLL_ATTEMPTS) return false;
      return POLL_INTERVAL_MS;
    },
  });

  // TanStack Query itself cancels the in-flight request (via the AbortSignal
  // threaded into fetchCurrentPostmortemReport above) and clears the
  // refetchInterval timer on unmount or on queryKey change (trade-ID
  // change/navigation) — no separate cleanup is needed here for that.
  useEffect(() => {
    if (query.data?.availability === "PROCESSING") {
      setPollCount((n) => n + 1);
    } else {
      setPollCount(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.dataUpdatedAt]);

  const timedOut = query.data?.availability === "PROCESSING" && pollCount >= MAX_POLL_ATTEMPTS;
  return { ...query, timedOut };
}

function WhyThisConclusion({ claim, evidenceById }: { claim: PostmortemClaim; evidenceById: Map<string, EvidenceItem> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded"
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
                return <span key={id} className="text-gray-300 mr-2 break-words">{ev ? `${ev.name}=${String(ev.value)}` : id}</span>;
              })}
            </div>
          )}
          {claim.opposing_evidence_ids.length > 0 && (
            <div>
              <span className="text-gray-500">Opposing: </span>
              {claim.opposing_evidence_ids.map((id) => {
                const ev = evidenceById.get(id);
                return <span key={id} className="text-bear mr-2 break-words">{ev ? `${ev.name}=${String(ev.value)}` : id}</span>;
              })}
            </div>
          )}
          {claim.missing_evidence.length > 0 && (
            <div>
              <span className="text-gray-500">Missing: </span>
              <span className="text-gray-400 break-words">{claim.missing_evidence.join("; ")}</span>
            </div>
          )}
          {claim.limitations.length > 0 && (
            <div>
              <span className="text-gray-500">Limitations: </span>
              <span className="text-gray-400 break-words">{claim.limitations.join("; ")}</span>
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
      <div className="flex items-start gap-1.5 flex-wrap">
        <span className={clsx("text-[10px] font-semibold uppercase shrink-0 mt-0.5", EVIDENCE_CLASS_STYLE[claim.evidence_class])}>
          {EVIDENCE_CLASS_LABEL[claim.evidence_class]}
        </span>
        {claim.evidence_class === "INSUFFICIENT_EVIDENCE" && (
          <InsufficientEvidenceMark reason={claim.missing_evidence.join("; ") || claim.claim_text} />
        )}
      </div>
      <div className="text-xs text-gray-300 mt-0.5 break-words">{claim.claim_text}</div>
      {claim.contradiction_flags.length > 0 && (
        // Labelled distinctly from ordinary "Limitations" — a contradiction
        // flag means the evidence actively disagrees with itself, a
        // materially different signal than merely incomplete evidence.
        <div className="text-[11px] text-bear mt-0.5 break-words">
          <span className="font-semibold">Contradictions: </span>
          {claim.contradiction_flags.join("; ")}
        </div>
      )}
      <WhyThisConclusion claim={claim} evidenceById={evidenceById} />
    </div>
  );
}

// One row per evidence item — source/verification/freshness, never just
// the bare value, so a reader can judge how much to trust it.
function EvidenceRow({ item }: { item: EvidenceItem }) {
  return (
    <div className="flex items-start justify-between gap-2 py-1 text-xs border-b border-dark-border/40 last:border-0">
      <div className="min-w-0">
        <div className="text-gray-300 break-words">
          {item.name}: <span className="font-mono">{safeFormatValue(item.value)}{item.units ? ` ${item.units}` : ""}</span>
        </div>
        <div className="text-[10px] text-gray-500">
          {item.source} · {item.source_type.replace(/_/g, " ").toLowerCase()} ·{" "}
          {item.verification_level.replace(/_/g, " ").toLowerCase()} ·{" "}
          {item.freshness_status.replace(/_/g, " ").toLowerCase()}
          {item.observation_timestamp && <> · {new Date(item.observation_timestamp).toLocaleString()}</>}
        </div>
        {item.limitations.length > 0 && (
          <div className="text-[10px] text-gray-500 break-words">Limitations: {item.limitations.join("; ")}</div>
        )}
      </div>
    </div>
  );
}

function NonReadyState({ availability, timedOut }: { availability: CurrentReportReadResponse["availability"]; timedOut: boolean }) {
  // Every governed non-READY state gets an explicit, named message — never
  // a generic blank page for a state the backend deliberately distinguishes.
  if (availability === "PROCESSING") {
    if (timedOut) {
      return (
        <div role="status" className="text-sm text-gray-400 py-10 text-center">
          This report is taking longer than expected to generate. Refresh the page in a little while to check again.
        </div>
      );
    }
    return (
      <div role="status" aria-live="polite" className="text-sm text-gray-400 py-10 text-center">
        Generating this trade&apos;s postmortem report… this page will update automatically.
      </div>
    );
  }
  if (availability === "NOT_ELIGIBLE") {
    return (
      <div role="status" className="text-sm text-gray-400 py-10 text-center">
        This trade is still open. A postmortem report is only available once a trade is closed.
      </div>
    );
  }
  if (availability === "NOT_AVAILABLE") {
    return (
      <div role="status" className="text-sm text-gray-400 py-10 text-center">
        No postmortem report has been generated for this trade yet.
      </div>
    );
  }
  if (availability === "TERMINAL_FAILURE") {
    return (
      <div role="alert" className="text-sm text-bear py-10 text-center">
        Report generation for this trade failed and will not be retried automatically. This is not a reflection of
        the trade&apos;s outcome — only of the report itself.
      </div>
    );
  }
  if (availability === "INTEGRITY_CONTRADICTION") {
    return (
      <div role="alert" className="text-sm text-bear py-10 text-center">
        This trade&apos;s report could not be verified as complete and consistent, so it is not being shown. No
        conclusion is being fabricated in its place.
      </div>
    );
  }
  if (availability === "FEATURE_DISABLED") {
    return (
      <div role="status" className="text-sm text-gray-400 py-10 text-center">
        Postmortem reports aren&apos;t available yet on this deployment.
      </div>
    );
  }
  return null;
}

export default function TradePostmortemPage() {
  const { user } = useAuth();
  const params = useParams<{ tradeId: string }>();
  const tradeId = Number(params?.tradeId);
  const tradeIdIsValid = Number.isFinite(tradeId) && tradeId > 0;

  const flagEnabled = isTradePostmortemPricePathEnabled();
  const { data, isLoading, isError, timedOut } = useBoundedProcessingPoll(
    tradeId,
    flagEnabled && !!user && tradeIdIsValid
  );

  if (!flagEnabled) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center">
        <h1 className="text-lg font-semibold text-gray-300">Postmortem reports aren&apos;t available yet</h1>
        <p className="text-sm text-gray-500 mt-2">This feature isn&apos;t enabled on this deployment yet.</p>
      </div>
    );
  }

  if (!tradeIdIsValid) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-16 text-center" role="alert">
        <h1 className="text-lg font-semibold text-gray-300">Invalid trade</h1>
      </div>
    );
  }

  if (!user) {
    return <div className="max-w-4xl mx-auto px-4 py-8 text-gray-400">Sign in to view this trade&apos;s postmortem report.</div>;
  }

  if (isLoading) {
    return <div role="status" aria-live="polite" className="max-w-4xl mx-auto px-4 py-16 text-sm text-gray-400 text-center">Loading…</div>;
  }

  if (isError) {
    // Never render the raw Axios/backend/auth error text (message, status
    // code, response body) — a single safe, generic sentence regardless of
    // the underlying failure (401/403/404/network/etc).
    return (
      <div role="alert" className="max-w-4xl mx-auto px-4 py-16 text-sm text-bear text-center">
        Could not load this report.
      </div>
    );
  }

  if (!data) {
    return null;
  }

  if (data.availability !== "READY") {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <h1 className="text-xl font-bold mb-4">Trade Postmortem</h1>
        <NonReadyState availability={data.availability} timedOut={timedOut} />
      </div>
    );
  }

  return <ReadyReport report={data} />;
}

function ReadyReport({ report }: { report: CurrentReportReadResponse }) {
  const [provenanceOpen, setProvenanceOpen] = useState(false);
  const structuredReport = report.structured_report;
  const postmortem = structuredReport?.postmortem;
  const pricePath = structuredReport?.price_path;
  const versionAndProvenance = pricePath?.version_and_provenance;

  const claims = report.claims ?? [];
  const evidenceItems = report.evidence_items ?? [];
  const evidenceById = new Map(evidenceItems.map((e) => [e.evidence_id, e]));

  const outcome = typeof postmortem === "object" && postmortem !== null ? asStr((postmortem as Record<string, JSONValue>).outcome) : null;
  const realizedPnlAbs = typeof postmortem === "object" && postmortem !== null ? asNum((postmortem as Record<string, JSONValue>).realized_pnl_abs) : null;
  const realizedPnlPct = typeof postmortem === "object" && postmortem !== null ? asNum((postmortem as Record<string, JSONValue>).realized_pnl_pct) : null;
  const exitMechanism = typeof postmortem === "object" && postmortem !== null ? asStr((postmortem as Record<string, JSONValue>).exit_mechanism) : null;

  const mfeAbs = asNum(pricePath?.mfe_abs);
  const mfePct = asNum(pricePath?.mfe_pct);
  const maeMagnitudeAbs = asNum(pricePath?.mae_magnitude_abs);
  const maeMagnitudePct = asNum(pricePath?.mae_magnitude_pct);
  const targetTouch = asBool(pricePath?.target_touch);
  const targetTouchType = asStr(pricePath?.target_touch_type);
  const stopTouch = asBool(pricePath?.stop_touch);
  const stopTouchType = asStr(pricePath?.stop_touch_type);
  const touchOrder = asStr(pricePath?.touch_order);
  const priceLimitations = Array.isArray(pricePath?.price_path_limitations)
    ? (pricePath!.price_path_limitations as JSONValue[]).filter((v): v is string => typeof v === "string")
    : [];
  const evidenceGaps = report.evidence_gaps ?? [];
  const warnings = report.warnings ?? [];

  // Deliberately does NOT include source_manifest.exit_trigger_timing_verification:
  // that field describes whether the CLOSE TRIGGER was independently
  // server-verified, not whether MFE/MAE/touch fields are computable — see
  // postmortemAvailabilityReason.ts's module comment for the defect this
  // fixes. The client-reported/unverified-trigger nuance is surfaced
  // separately below via isUnverifiedTargetHitCloseCase, scoped to the
  // specific case it actually describes.
  const reasonFor = (value: unknown) =>
    resolveAvailabilityReason({
      value,
      reportAvailability: report.availability ?? null,
      pricePathLimitations: priceLimitations,
      evidenceGaps,
      warnings,
    });

  const isLimited = report.status === "LIMITED_EVIDENCE";
  const currencySymbol = currencySymbolFor(report.market);
  const closureClassification = findClosureClassification(evidenceItems);

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-5">
      <div>
        <h1 className="text-xl font-bold">Trade Postmortem</h1>
        {/* Stock identity — DISPLAY metadata only, never governed
            evidence. company_name is the primary readable title when
            available; symbol always remains visible alongside it. Never
            "Unknown stock" when the symbol is known, never a live quote
            fetched merely to show the name. */}
        {report.symbol && (
          <p className="text-base font-semibold text-gray-100 mt-1 break-words" data-testid="stock-identity">
            {report.company_name ? `${report.company_name} (${report.symbol})` : report.symbol}
          </p>
        )}
        <p className="text-sm text-gray-500 mt-1">
          {report.market ?? "—"} · Report date {report.report_trading_date ?? "—"}
          {report.generated_at && <> · Generated {new Date(report.generated_at).toLocaleString()}</>}
        </p>
      </div>

      {isLimited && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200" role="status">
          <span className="font-semibold">Limited evidence.</span> Some of the analysis below could not be
          completed with the available evidence. This report presents what is known without implying certainty
          about what is not.
        </div>
      )}

      {/* Layer 1 — Executive Summary: one deterministic plain-English
          paragraph built only from governed report fields. */}
      <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-3 text-sm text-gray-200" data-testid="layer1-summary">
        {buildLayer1Summary(report)}
      </div>

      <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
        <div>
          <div className="text-gray-500">Outcome</div>
          <div className="font-semibold text-sm">{outcome ? outcome.replace(/_/g, " ") : "N/A"}</div>
        </div>
        <div>
          <div className="text-gray-500">Realized P&amp;L</div>
          <div className={clsx("font-semibold text-sm tabular-nums", realizedPnlAbs !== null && (realizedPnlAbs >= 0 ? "text-bull" : "text-bear"))}>
            {fmtAbs(realizedPnlAbs, currencySymbol)} ({fmtPct(realizedPnlPct)})
          </div>
        </div>
        <div>
          <div className="text-gray-500">Exit mechanism</div>
          <div className="font-semibold text-sm">{exitMechanism ? exitMechanism.replace(/_/g, " ") : "N/A"}</div>
        </div>
        <div>
          <div className="text-gray-500">Evidence</div>
          <div className="font-semibold text-sm">{report.status ?? "N/A"}</div>
        </div>
        <div>
          {/* Sourced ONLY from the governed EXIT/closure_classification
              evidence item — never inferred from P&L, exit mechanism,
              exit reason, touch evidence, or claim text (see
              findClosureClassification's own comment). */}
          <div className="text-gray-500">Closure classification</div>
          <div className="font-semibold text-sm">{closureClassification ? closureClassification.replace(/_/g, " ") : "N/A"}</div>
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-gray-300 mb-2">Price path</h2>
        <div className="rounded-lg border border-dark-border bg-dark-card px-4 py-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-gray-500">Max favorable excursion</div>
            <div className="font-medium tabular-nums">{fmtAbs(mfeAbs, currencySymbol)} ({fmtPct(mfePct)})</div>
            {mfeAbs === null && (
              <div className="text-[10px] text-gray-500 mt-0.5 break-words" data-testid="mfe-reason">
                {reasonFor(mfeAbs)}
              </div>
            )}
          </div>
          <div>
            <div className="text-gray-500">Max adverse excursion</div>
            <div className="font-medium tabular-nums">{fmtAbs(maeMagnitudeAbs, currencySymbol)} ({fmtPct(maeMagnitudePct)})</div>
            {maeMagnitudeAbs === null && (
              <div className="text-[10px] text-gray-500 mt-0.5 break-words" data-testid="mae-reason">
                {reasonFor(maeMagnitudeAbs)}
              </div>
            )}
          </div>
          <div>
            <div className="text-gray-500">Target touched</div>
            <div className="font-medium">
              {targetTouch === null ? "N/A" : targetTouch ? (targetTouchType ?? "Yes") : "No"}
            </div>
            {targetTouch === null && (
              <div className="text-[10px] text-gray-500 mt-0.5 break-words" data-testid="target-touch-reason">
                {reasonFor(targetTouch)}
              </div>
            )}
          </div>
          <div>
            <div className="text-gray-500">Stop touched</div>
            <div className="font-medium">
              {stopTouch === null ? "N/A" : stopTouch ? (stopTouchType ?? "Yes") : "No"}
            </div>
            {stopTouch === null && (
              <div className="text-[10px] text-gray-500 mt-0.5 break-words" data-testid="stop-touch-reason">
                {reasonFor(stopTouch)}
              </div>
            )}
          </div>
        </div>
        {touchOrder && (
          <p className="text-xs text-gray-500 mt-1.5">Touch sequence: {touchOrder.replace(/_/g, " ").toLowerCase()}</p>
        )}
        {isUnverifiedTargetHitCloseCase(exitMechanism, targetTouch) && (
          <p role="note" className="text-xs text-amber-200 mt-1.5 break-words rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5">
            {UNVERIFIED_TARGET_HIT_EXPLANATION}
          </p>
        )}
        {priceLimitations.length > 0 && (
          <p className="text-xs text-gray-500 mt-1.5 break-words">Price-path limitations: {priceLimitations.join("; ")}</p>
        )}
      </div>

      {/* Layer 2 — Investor Explanation: claims grouped by report_section,
          each with a named factor title, text+icon evidence-class label,
          plain-language reason, and a "What You Can Learn" rollup. Every
          claim in report.claims renders exactly once here. */}
      <div>
        <h2 className="text-sm font-semibold text-gray-300 mb-2">What This Report Found</h2>
        <ClaimExplanationLayer
          claims={claims}
          evidenceById={evidenceById}
          pricePathTopLevelValues={{
            mfe: mfeAbs, mae: maeMagnitudeAbs, target_touch: targetTouch, stop_touch: stopTouch,
          }}
        />
      </div>

      {/* Layer 3 — Technical Evidence & Audit Trail: original claim text,
          rule metadata, and raw evidence items, all preserved verbatim and
          expandable. ClaimRow below is retained for this raw/audit view. */}
      <details className="text-xs">
        <summary className="text-gray-400 cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded">
          Technical evidence &amp; audit trail — all claims ({claims.length})
        </summary>
        <div className="mt-2 rounded-lg border border-dark-border bg-dark-card px-4 py-2 max-h-96 overflow-y-auto">
          {claims.map((c) => <ClaimRow key={c.claim_id} claim={c} evidenceById={evidenceById} />)}
        </div>
      </details>

      {evidenceItems.length > 0 && (
        <details className="text-xs">
          <summary className="text-gray-400 cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded">
            All evidence ({evidenceItems.length})
          </summary>
          <div className="mt-2 rounded-lg border border-dark-border bg-dark-card px-3 py-2 max-h-96 overflow-y-auto">
            {evidenceItems.map((e) => <EvidenceRow key={e.evidence_id} item={e} />)}
          </div>
        </details>
      )}

      {(report.evidence_gaps?.length ?? 0) > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 mb-1">Evidence gaps</h2>
          <ul className="text-xs text-gray-500 list-disc list-inside space-y-0.5">
            {report.evidence_gaps!.map((g, i) => <li key={i} className="break-words">{g}</li>)}
          </ul>
        </div>
      )}

      {(report.warnings?.length ?? 0) > 0 && (
        <div>
          <h2 className="text-xs font-semibold text-gray-400 mb-1">Warnings</h2>
          <ul className="text-xs text-gray-500 list-disc list-inside space-y-0.5">
            {report.warnings!.map((w, i) => <li key={i} className="break-words">{w}</li>)}
          </ul>
        </div>
      )}

      {versionAndProvenance && (
        <div className="text-xs">
          <button
            type="button"
            onClick={() => setProvenanceOpen((o) => !o)}
            aria-expanded={provenanceOpen}
            className="text-gray-400 hover:text-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded"
          >
            {provenanceOpen ? "Hide" : "Show"} version &amp; provenance details
          </button>
          {provenanceOpen && (
            <dl className="mt-2 rounded-lg border border-dark-border bg-dark-card px-3 py-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500">
              <dt>Report schema</dt><dd className="text-gray-300">{versionAndProvenance.report_schema_version}</dd>
              <dt>Calculation</dt><dd className="text-gray-300">{versionAndProvenance.calculation_version}</dd>
              <dt>Numerical rules</dt><dd className="text-gray-300">{versionAndProvenance.numerical_rules_version}</dd>
              <dt>Semantic rules</dt><dd className="text-gray-300">{versionAndProvenance.governed_semantic_rules_version}</dd>
              <dt>Claim rules</dt><dd className="text-gray-300">{versionAndProvenance.governed_claim_rules_version}</dd>
              <dt>Source version</dt><dd className="text-gray-300">{versionAndProvenance.source_version}</dd>
              <dt>Entry snapshot schema</dt><dd className="text-gray-300">{versionAndProvenance.entry_snapshot_schema_version ?? "N/A"}</dd>
              <dt>Exit snapshot schema</dt><dd className="text-gray-300">{versionAndProvenance.exit_snapshot_schema_version ?? "N/A"}</dd>
              <dt>Level history contract</dt><dd className="text-gray-300">{versionAndProvenance.level_history_contract_version ?? "N/A"}</dd>
              {report.supersedes_report_id !== null && (
                <>
                  <dt>Supersedes report</dt><dd className="text-gray-300">#{report.supersedes_report_id}</dd>
                </>
              )}
            </dl>
          )}
        </div>
      )}

      <ReportEvidenceManifest sourceManifest={report.source_manifest} supersedesReportId={report.supersedes_report_id} />
    </div>
  );
}

// Wave C, WC-N §5 — a clearly separated disclosure from the version/
// provenance section above, using ONLY the explicitly typed
// ReportSourceManifest fields (never enumerating or displaying unknown
// extra keys the backend's extra="allow" contract may carry — those are
// genuinely out of scope for this governed view).
function ReportEvidenceManifest({
  sourceManifest, supersedesReportId,
}: {
  sourceManifest: CurrentReportReadResponse["source_manifest"];
  supersedesReportId: number | null;
}) {
  const [open, setOpen] = useState(false);
  if (!sourceManifest) return null;

  // price_path_calculation_version is historical-optional — genuinely
  // absent (not present as a key) on a report generated before or
  // without price-path enhancement. Never display an invented null row
  // for it; only render the row when the key is truly present.
  const hasPricePathCalculationVersion = "price_path_calculation_version" in sourceManifest
    && sourceManifest.price_path_calculation_version !== undefined;

  return (
    <div className="text-xs">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="text-gray-400 hover:text-gray-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 rounded"
      >
        {open ? "Hide" : "Show"} report evidence manifest
      </button>
      {open && (
        <dl className="mt-2 rounded-lg border border-dark-border bg-dark-card px-3 py-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-gray-500">
          <dt>Entry snapshot available</dt><dd className="text-gray-300">{sourceManifest.has_entry_snapshot ? "Yes" : "No"}</dd>
          <dt>Exit snapshot available</dt><dd className="text-gray-300">{sourceManifest.has_exit_snapshot ? "Yes" : "No"}</dd>
          <dt>Exit snapshot schema</dt><dd className="text-gray-300">{sourceManifest.exit_snapshot_schema_version ?? "N/A"}</dd>
          <dt>Exit trigger timing verification</dt>
          <dd className="text-gray-300">{sourceManifest.exit_trigger_timing_verification?.replace(/_/g, " ") ?? "N/A"}</dd>
          <dt>Exit evidence rules</dt><dd className="text-gray-300">{sourceManifest.exit_evidence_rules_version}</dd>
          <dt>Phase 1 calculation</dt><dd className="text-gray-300">{sourceManifest.phase1_calculation_version}</dd>
          <dt>Attribution rules</dt><dd className="text-gray-300">{sourceManifest.attribution_rules_version}</dd>
          {hasPricePathCalculationVersion && (
            <>
              <dt>Price-path calculation</dt><dd className="text-gray-300">{sourceManifest.price_path_calculation_version}</dd>
            </>
          )}
          <dt>Price-path rules</dt><dd className="text-gray-300">{sourceManifest.price_path_rules_version}</dd>
          <dt>Governed rules</dt><dd className="text-gray-300">{sourceManifest.governed_rules_version}</dd>
          {supersedesReportId !== null && (
            <>
              <dt>Supersedes report</dt><dd className="text-gray-300">#{supersedesReportId}</dd>
            </>
          )}
        </dl>
      )}
    </div>
  );
}
