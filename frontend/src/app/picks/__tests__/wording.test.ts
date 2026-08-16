import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  derivePublicationPolicy,
  formatPublicationPolicyCopy,
} from "../page";

// Learning Alpha Engine remediation, Phase 1 — Investor-facing disclosure.
// picks/page.tsx renders through a data-fetching component that isn't
// practically mountable in isolation here (live market-hours checks, SWR
// fetches, etc.), so static, unconditional wording is locked in as a
// source-text assertion. Conditional/derived wording (finding 2/4,
// follow-up to commit 5a006498) is instead tested against the exported
// pure functions (derivePublicationPolicy/formatPublicationPolicyCopy)
// with realistic payload shapes, so it verifies actual behavior — not just
// that a string appears somewhere in the file.
// Path is relative to the frontend package root (vitest's process.cwd()).
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Daily Picks investor-facing wording (Phase 1 disclosure)", () => {
  it("labels the confidence percentage as Signal Strength", () => {
    expect(pageSource).toContain("Signal Strength");
  });

  it("no longer uses the retired 'AI Confidence' label", () => {
    expect(pageSource).not.toContain("AI Confidence");
  });

  it("discloses that Signal Strength is not a guaranteed probability of profit", () => {
    expect(pageSource).toContain("Signal Strength is not a guaranteed probability of profit.");
  });

  it("labels the model-generated target as Scenario Target", () => {
    expect(pageSource).toContain("Scenario Target");
  });
});

describe("Daily Picks conviction-gated publication wording (static)", () => {
  it("no longer claims a fixed six picks per horizon", () => {
    expect(pageSource).not.toContain("Top 6");
  });

  it("does not hardcode the 3/85 policy numbers directly in the header string", () => {
    // The header must be built via formatPublicationPolicyCopy(), not a
    // hardcoded "Up to 3 ... >= 85/100" template literal in the page body.
    expect(pageSource).not.toContain("Up to 3 qualified picks per horizon (Model Conviction");
  });

  it("references Model Conviction consistently", () => {
    expect(pageSource).toContain("Model Conviction");
  });

  it("displays the score out of 100, not as a bare percentage label", () => {
    expect(pageSource).toContain("{pick.confidence}/100");
  });

  it("does not describe Model Conviction as a probability or % chance", () => {
    expect(pageSource).not.toMatch(/\d+%\s*chance/i);
    expect(pageSource).not.toMatch(/conviction[^.]*probability of (a )?win/i);
  });

  it("no longer describes the historical score-bucket table as 'calibration' or a 'calibrated signal'", () => {
    expect(pageSource).not.toContain("Signal Strength Calibration —");
    expect(pageSource).not.toMatch(/that'?s a calibrated signal/i);
  });

  it("no longer uses '% AI confidence' anywhere in the page", () => {
    expect(pageSource).not.toMatch(/%\s*AI confidence/i);
  });

  it("historical score-bucket panel is framed as observed reliability, not calibration", () => {
    expect(pageSource).toContain("Observed Historical Reliability by Model Conviction Band");
  });

  it("the High Conviction Only control is gated off once the backend publication policy is active", () => {
    // Guards against the redundant/misleading control finding 3 flags: an
    // active `>=85` client filter layered on an already `>=85/100`
    // backend-gated, <=3-per-horizon list. Must be conditionally rendered
    // on `!publicationPolicy`, not unconditionally shown.
    expect(pageSource).toMatch(/\{!publicationPolicy\s*&&\s*\(/);
  });
});

const VALID_SEMANTIC = "Model Conviction (0-100 scale, not a calibrated win probability)";

function validMeta(overrides: Record<string, unknown> = {}) {
  return {
    n_scored: 400, n_buy: 12,
    n_conviction_qualified: 5, n_published: 3,
    conviction_threshold: 85, max_published_per_horizon: 3,
    conviction_semantic: VALID_SEMANTIC,
    ...overrides,
  };
}

describe("derivePublicationPolicy — actual behavior against realistic payload shapes", () => {
  it("returns null for a legacy payload with no alpha_engine_meta at all", () => {
    expect(derivePublicationPolicy(undefined)).toBeNull();
    expect(derivePublicationPolicy(null)).toBeNull();
  });

  it("returns null for a legacy payload with only the pre-existing n_scored/n_buy fields", () => {
    const legacy = { n_scored: 400, n_buy: 12, meta_model: true, regime: "bull" };
    expect(derivePublicationPolicy(legacy)).toBeNull();
  });

  it("returns null when publication fields are present but wrongly typed (defensive parsing)", () => {
    const malformed = validMeta({ n_conviction_qualified: "5" }) as unknown; // wrong type — string, not number
    expect(derivePublicationPolicy(malformed as never)).toBeNull();
  });

  it("returns null when only some publication fields are present (partial metadata)", () => {
    const partial = { n_scored: 400, n_buy: 12, n_published: 2 };
    expect(derivePublicationPolicy(partial)).toBeNull();
  });

  it("returns the derived policy for a valid, fully-typed publication payload", () => {
    expect(derivePublicationPolicy(validMeta())).toEqual({
      maxPublished: 3, threshold: 85, nPublished: 3, nQualified: 5,
      semantic: VALID_SEMANTIC,
    });
  });

  it("derives correctly for a zero-published horizon (legitimate outcome, not an error)", () => {
    const policy = derivePublicationPolicy(validMeta({ n_conviction_qualified: 0, n_published: 0 }));
    expect(policy?.nPublished).toBe(0);
    expect(policy?.maxPublished).toBe(3);
  });

  // Finding 2 (corrective follow-up to 0f2bbed8): typeof === "number" alone
  // let NaN/Infinity/negative/fractional values through. Every case below
  // must now fail closed (return null).
  it("returns null when conviction_threshold is NaN", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: NaN }))).toBeNull();
  });

  it("returns null when conviction_threshold is +Infinity", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: Infinity }))).toBeNull();
  });

  it("returns null when conviction_threshold is -Infinity", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: -Infinity }))).toBeNull();
  });

  it("returns null when conviction_threshold is below 0", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: -1 }))).toBeNull();
  });

  it("returns null when conviction_threshold is above 100", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: 100.01 }))).toBeNull();
  });

  it("accepts conviction_threshold at the 0 and 100 boundaries", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: 0 }))?.threshold).toBe(0);
    expect(derivePublicationPolicy(validMeta({ conviction_threshold: 100 }))?.threshold).toBe(100);
  });

  it("returns null when max_published_per_horizon is zero", () => {
    expect(derivePublicationPolicy(validMeta({ max_published_per_horizon: 0, n_published: 0 }))).toBeNull();
  });

  it("returns null when max_published_per_horizon is negative", () => {
    expect(derivePublicationPolicy(validMeta({ max_published_per_horizon: -3 }))).toBeNull();
  });

  it("returns null when max_published_per_horizon is fractional", () => {
    expect(derivePublicationPolicy(validMeta({ max_published_per_horizon: 3.5 }))).toBeNull();
  });

  it("returns null when n_published is negative", () => {
    expect(derivePublicationPolicy(validMeta({ n_published: -1 }))).toBeNull();
  });

  it("returns null when n_published is fractional", () => {
    expect(derivePublicationPolicy(validMeta({ n_published: 1.5 }))).toBeNull();
  });

  it("returns null when n_conviction_qualified is negative", () => {
    expect(derivePublicationPolicy(validMeta({ n_conviction_qualified: -1, n_published: 0 }))).toBeNull();
  });

  it("returns null when n_conviction_qualified is fractional", () => {
    expect(derivePublicationPolicy(validMeta({ n_conviction_qualified: 2.5 }))).toBeNull();
  });

  it("returns null when n_published exceeds max_published_per_horizon", () => {
    expect(derivePublicationPolicy(validMeta({ n_published: 4, n_conviction_qualified: 5, max_published_per_horizon: 3 }))).toBeNull();
  });

  it("returns null when n_published exceeds n_conviction_qualified", () => {
    expect(derivePublicationPolicy(validMeta({ n_published: 3, n_conviction_qualified: 2 }))).toBeNull();
  });

  it("returns null when conviction_semantic is missing", () => {
    const { conviction_semantic, ...rest } = validMeta();
    expect(derivePublicationPolicy(rest)).toBeNull();
  });

  it("returns null when conviction_semantic is an empty string", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_semantic: "" }))).toBeNull();
  });

  it("returns null when conviction_semantic is not a string", () => {
    expect(derivePublicationPolicy(validMeta({ conviction_semantic: 42 }))).toBeNull();
  });
});

describe("formatPublicationPolicyCopy — dynamic wording sourced from backend values", () => {
  it("renders the backend's own threshold/max, not a hardcoded 3/85", () => {
    const policy = { maxPublished: 3, threshold: 85, nPublished: 2, nQualified: 4, semantic: VALID_SEMANTIC };
    expect(formatPublicationPolicyCopy(policy)).toBe(
      "Up to 3 qualified picks per horizon (Model Conviction ≥ 85/100)"
    );
  });

  it("reflects a different backend-configured threshold/cap without a frontend code change", () => {
    const policy = { maxPublished: 5, threshold: 90, nPublished: 1, nQualified: 1, semantic: VALID_SEMANTIC };
    expect(formatPublicationPolicyCopy(policy)).toBe(
      "Up to 5 qualified picks per horizon (Model Conviction ≥ 90/100)"
    );
  });

  it("does not claim the new policy for a legacy/null policy — neutral copy instead", () => {
    const copy = formatPublicationPolicyCopy(null);
    expect(copy).not.toMatch(/up to \d+ qualified picks/i);
    expect(copy).not.toMatch(/model conviction/i);
    expect(copy).toBe("Qualified BUY picks per horizon");
  });
});
