"use client";
import { Info } from "lucide-react";
import clsx from "clsx";

/**
 * Daily Trade Postmortem Report — accessible inline marker for a factor the
 * backend explicitly could not assess (services/postmortem/causal_analysis.py
 * returns SignalDirectionAgreement.INSUFFICIENT_EVIDENCE, never a guess).
 *
 * A new, generic sibling to DataLimitationsMark (frontend/src/components/
 * DataLimitationsNotice.tsx) — deliberately not reused directly, since that
 * component's copy and href are hardcoded to the validation-page-specific
 * disclosure. Same accessibility pattern: a visible accessible name via
 * `aria-label` (not a hover-only tooltip), reachable and legible without a
 * pointer device.
 */
export function InsufficientEvidenceMark({ reason, className }: { reason: string; className?: string }) {
  return (
    <span
      role="img"
      aria-label={`Insufficient evidence: ${reason}`}
      title={reason}
      className={clsx(
        "inline-flex items-center justify-center text-amber-300/70 hover:text-amber-200",
        className
      )}
    >
      <Info size={11} aria-hidden="true" />
    </span>
  );
}

export default InsufficientEvidenceMark;
