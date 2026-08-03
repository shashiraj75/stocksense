/**
 * Wave C, WC-N — compile-time-only assertions that the governed
 * vocabularies in the current-report TypeScript contract actually reject
 * unsupported values. This file is never imported or executed at
 * runtime; its only job is to make `tsc --noEmit` fail if any of these
 * `@ts-expect-error` directives stops being a real error (i.e. if the
 * contract were accidentally widened back to a plain `string`).
 */
import type { ReportSourceManifest, EvidenceItem, VersionAndProvenance } from "@/utils/api";

declare const manifest: ReportSourceManifest;

// @ts-expect-error — TriggerTimingVerification rejects an unrecognized value.
manifest.exit_trigger_timing_verification = "SOMETHING_MADE_UP";

// A governed value is still accepted.
manifest.exit_trigger_timing_verification = "SERVER_VERIFIED";
manifest.exit_trigger_timing_verification = null;

declare const evidenceItem: EvidenceItem;

// @ts-expect-error — source_type rejects an unrecognized value.
evidenceItem.source_type = "MADE_UP_SOURCE";

// @ts-expect-error — verification_level rejects an unrecognized value.
evidenceItem.verification_level = "MADE_UP_LEVEL";

// value is bounded JSON, not `unknown` — a function is not valid JSON.
// @ts-expect-error — value must be JSONValue, not an arbitrary function.
evidenceItem.value = () => {};

declare const provenance: VersionAndProvenance;

// @ts-expect-error — an unexpected extra field is rejected (no index
// signature on VersionAndProvenance — mirrors the backend's extra="forbid").
provenance.unexpected_extra_field = "x";
