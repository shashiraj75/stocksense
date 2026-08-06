import { describe, it, expect } from "vitest";
import {
  resolveAvailabilityReason, isUnverifiedTargetHitCloseCase, UNVERIFIED_TARGET_HIT_EXPLANATION,
} from "@/utils/postmortemAvailabilityReason";

// Covers the 15-scenario matrix from the explainability-phase completion
// pass, focused on the resolver's structured-field-first precedence.
describe("resolveAvailabilityReason — precedence and scenario coverage", () => {
  it("1. COMPLETE report, value present -> Available regardless of any limitation text", () => {
    expect(
      resolveAvailabilityReason({
        value: 12.5, reportAvailability: "READY", pricePathLimitations: ["no compatible price bars"],
        evidenceGaps: [], warnings: [],
      })
    ).toBe("Available");
  });

  it("2. LIMITED_EVIDENCE status (report.availability is still READY) -> exact-match table, not fallback", () => {
    // LIMITED_EVIDENCE is a report.status (completeness) value, not a
    // report.availability (lifecycle) value — a LIMITED_EVIDENCE report is
    // still availability=READY, so tier 2 must not fire here.
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [], evidenceGaps: ["entry evidence completeness is LIMITED"], warnings: [],
      })
    ).toBe("Not captured at entry");
  });

  it("3. TARGET_HIT with target_touch=null is explained without implying contradiction", () => {
    expect(isUnverifiedTargetHitCloseCase("TARGET_HIT", null)).toBe(true);
    expect(UNVERIFIED_TARGET_HIT_EXPLANATION.toLowerCase()).toContain("not a contradiction");
  });

  it("4. Client-reported unverified trigger -> surfaced only via isUnverifiedTargetHitCloseCase, never as a blanket price-path reason", () => {
    // Regression test for a real defect found during preview QA: an
    // earlier version of the resolver treated
    // source_manifest.exit_trigger_timing_verification=CLIENT_REPORTED_UNVERIFIED
    // as a blanket tier applied to ALL FOUR price-path fields (MFE, MAE,
    // target touch, stop touch), so MFE/MAE incorrectly showed
    // "Client-reported only" even when the real reason was incomplete
    // provider coverage. The resolver no longer accepts that field at
    // all — the trigger-verification nuance is surfaced only via
    // isUnverifiedTargetHitCloseCase, scoped to the specific TARGET_HIT +
    // target_touch=null case it actually describes.
    expect(isUnverifiedTargetHitCloseCase("TARGET_HIT", null)).toBe(true);
    const result = resolveAvailabilityReason({
      value: null, reportAvailability: "READY",
      pricePathLimitations: [], evidenceGaps: [], warnings: [],
    });
    expect(result).not.toBe("Client-reported only");
    expect(result).not.toMatch(/server[- ]verified/i);
  });

  it("5. MFE/MAE must not receive a client-reported reason merely because the exit trigger was client-reported", () => {
    // Direct regression coverage for the reported bug: given the exact
    // real-world scenario (provider coverage gap causing MFE/MAE to be
    // null, on a trade whose close trigger was client-reported), the
    // resolver must resolve MFE/MAE from the price-path limitation text,
    // never from the unrelated trigger-verification field.
    const result = resolveAvailabilityReason({
      value: null, reportAvailability: "READY",
      pricePathLimitations: [
        "requested window 2026-08-04..2026-08-05 but provider only returned bars for 2026-08-05..2026-08-05",
      ],
      evidenceGaps: [], warnings: [],
    });
    expect(result).toBe("Provider returned incomplete coverage");
    expect(result).not.toBe("Client-reported only");
  });

  it("6. Missing price-provider sessions -> exact-match table (provider returned no valid bars)", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: ["no valid bars were returned by the provider for the requested window"],
        evidenceGaps: [], warnings: [],
      })
    ).toBe("Provider returned incomplete coverage");
  });

  it("6b. Missing price-provider sessions — dynamically-templated variant (tier 5 bounded substring)", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [
          "requested window 2026-08-04..2026-08-05 but provider only returned bars for 2026-08-05..2026-08-05",
        ],
        evidenceGaps: [], warnings: [],
      })
    ).toBe("Provider returned incomplete coverage");
  });

  it("7. No entry snapshot -> Not captured at entry (exact match, not prefix drift)", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [], evidenceGaps: ["no entry snapshot exists for this trade"], warnings: [],
      })
    ).toBe("Not captured at entry");
  });

  it("8. Contradictory evidence is not the resolver's concern — it never claims Available for a contradiction", () => {
    // Contradictions are rendered distinctly by ClaimExplanationLayer's
    // contradiction_flags path, not by this resolver — verify the resolver
    // still falls through safely (not silently "Available") when value is
    // absent even though other fields are populated.
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY", pricePathLimitations: [], evidenceGaps: [], warnings: [],
      })
    ).toBe("Reason unavailable was not supplied by this report.");
  });

  it("9. Non-READY availability state -> Not applicable (tier 2, before any text matching)", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "PROCESSING",
        pricePathLimitations: ["entry evidence completeness is LIMITED"], evidenceGaps: [], warnings: [],
      })
    ).toBe("Not applicable");
  });

  it("13. Legacy snapshot schema (historical trade) -> Historical evidence unavailable", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [],
        evidenceGaps: [
          "no exit snapshot exists for this trade — this is a historical trade closed before the durable close lifecycle existed",
        ],
        warnings: [],
      })
    ).toBe("Historical evidence unavailable");
  });

  it("14. No claims / no limitation text anywhere -> final safe fallback, never invented", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY", pricePathLimitations: [], evidenceGaps: [], warnings: [],
      })
    ).toBe("Reason unavailable was not supplied by this report.");
  });

  it("15. Malformed/absent optional price-path value (undefined) is treated identically to null", () => {
    expect(
      resolveAvailabilityReason({
        value: undefined, reportAvailability: "READY", pricePathLimitations: [], evidenceGaps: [], warnings: [],
      })
    ).toBe("Reason unavailable was not supplied by this report.");
  });

  it("incidental wording change does NOT silently produce a wrong category — falls through to fallback instead", () => {
    // A near-miss of a real governed string (e.g. reworded by a future
    // backend change) must not fuzzy-match into the wrong category — the
    // exact/prefix table only matches the strings it actually knows.
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [], evidenceGaps: ["entry evidence is somewhat incomplete for this trade"], warnings: [],
      })
    ).toBe("Reason unavailable was not supplied by this report.");
  });

  it("boundary-verification limitation resolves to 'Boundary could not be verified', not a generic fallback", () => {
    expect(
      resolveAvailabilityReason({
        value: null, reportAvailability: "READY",
        pricePathLimitations: [
          "no compatible crossing of the supplied value was observed in the acquired evidence window, for a level reliably unmodified since entry",
        ],
        evidenceGaps: [], warnings: [],
      })
    ).toBe("Boundary could not be verified");
  });
});
