# Sprint #005 — Recommendation Consolidation Structural Coverage Narrative Refinement (Epic 005)

**Status:** Complete. A narrow narrative-semantics and coverage-classification refinement sprint — not a live integration sprint. RCI remains additive, read-only, deterministic, non-authoritative, not wired into Prediction Engine, Daily Picks, APIs, persistence, or UI.

## Evidence Checkpoint (Mandatory)

Reviewed Sprints #001–#004's documents and code directly. **All required invariants reconfirmed true**: RCI remains additive, read-only, deterministic, non-authoritative, creates no master score/replacement signal/replacement confidence, is not integrated anywhere, does not consume legacy `growth_score`/`valuation_score`, does not alter hard-gate behavior, kill switches and fraud-risk enforcement remain unchanged, no new provider required.

**Confirmed the actual pattern identifier by direct code inspection, not assumption**: the India-side pattern is `CP-07-missing-engine`, defined in `recommendation_consolidation_engine.py`'s `_detect_conflicts()` function (prior to this sprint's change, at line 114). **Confirmed the root cause directly, not assumed**: the condition `e.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE)` treated both statuses identically. `adapt_financial_strength()`'s own `applicable=(market == "US")` parameter confirms Financial Strength is **unconditionally** `NOT_APPLICABLE` for every India company — a permanent, market-level fact, not a company-specific data absence, temporary provider failure, execution error, or feature-disabled state. **No prior documentation contradicts this conclusion** — Sprint #002's own Evidence Contract already named India's Financial Strength absence as structural; this sprint's defect was in the *consolidation engine's* treatment of that already-correctly-classified status, not in the classification itself.

## Required Availability Classification Review

**The existing contract already supports a clean distinction — no new framework was needed.** `EvidenceStatus.NOT_APPLICABLE` (already existed, Sprint #003) combined with the adapter's own `reason_code="not_applicable_for_market"` (already set for the Financial-Strength-in-India case specifically, confirmed by direct inspection of `_normalize()`'s "not applicable" branch) **already discriminates market-structural unavailability from every other case** — confirmed directly, not assumed:

| Brief's suggested category | Mapped to existing contract field |
|---|---|
| `market_structural_unavailability` | `status=NOT_APPLICABLE` + `reason_code="not_applicable_for_market"` (already present) |
| `company_specific_unavailability` | `status=UNAVAILABLE` (already present) |
| `not_applicable` (sector-specific) | `status=NOT_APPLICABLE` with a *different* `reason_code` (none currently exists, but the field already supports adding one without a contract version bump) |
| `execution_error` | `status=EXECUTION_ERROR` (already present) |
| `feature_disabled` | `status=FEATURE_DISABLED` (already present) |
| `stale_snapshot` | `status=STALE_SNAPSHOT` (already present, not yet exercised by any live code path) |
| `unknown_unavailability` | Not implemented — no real case in 274 real companies (Sprint #004) ever produced an unclassifiable cause; adding a placeholder for a hypothetical would violate this sprint's own "smallest correct implementation" instruction |

**No new `EvidenceStatus` value, no new `HardGateType`, and no contract-version bump was required.** The only genuine gap was in `recommendation_consolidation_engine.py`'s *logic*, not in `recommendation_consolidation_contract.py`'s *data shapes*.

## Chosen Refinement Approach

**A combination of the brief's Options B and C**, the smallest evidence-supported change:

1. **(Option C)** `CP-07`'s detection condition is narrowed to `UNAVAILABLE`/`EXECUTION_ERROR` only — genuine, company-specific causes. `NOT_APPLICABLE` no longer contributes to this pattern at all.
2. **(Option B)** A new, separate output field, `coverage_notices: tuple[str, ...]`, surfaces market-structural unavailability (`NOT_APPLICABLE` + `reason_code="not_applicable_for_market"`) — deterministic, cautious, evidence-led wording, **never counted toward `conflicts`, `thesis_state`, `engine_agreement`, `supporting_evidence`, or `opposing_evidence`**.

**Rejected: Option A (suppress entirely)** — silently dropping the information would violate the brief's own requirement that "the future user-facing explanation must remain transparent about coverage limitations." The information remains available, just correctly categorized and never repeated as if it were a per-company finding.

## Contract Changes

| Change | File | Backward compatible? |
|---|---|---|
| `coverage_notices: tuple[str, ...]` added to `RecommendationConsolidationResponse` | `recommendation_consolidation_contract.py` | **Yes** — purely additive, per the Traceability document's own existing backward-compatibility rule; no `contract_version` bump required since no existing field's meaning changed |
| `CP-07`'s detection condition narrowed | `recommendation_consolidation_engine.py` | **Yes for all genuine cases** — confirmed via the live spot-check below; the only behavior change is the removal of a false-positive-equivalent pattern firing, not a removal of real information |

## Structural vs. Company-Specific Availability Model (as implemented)

```
status=NOT_APPLICABLE, reason_code="not_applicable_for_market"
  -> coverage_notices (NEW) -- platform-level, never a conflict, never opposing evidence

status=UNAVAILABLE or EXECUTION_ERROR
  -> CP-07-missing-engine (unchanged conflict pattern) -- genuine, company-specific

status=FEATURE_DISABLED
  -> cited for context only, never coverage_notices, never CP-07 (unchanged from Sprint #003/#004)

status=STALE_SNAPSHOT
  -> not yet exercised by any live code path (no snapshot-storage integration exists)
```

## Gate and Provenance Revalidation

Re-confirmed, with 4 new dedicated tests (`TestGateAndProvenanceSemanticsPreserved`), that this sprint's changes did not weaken Sprint #004's corrections:
- Financial Strength `liquidity_distress` remains in `active_gates` only (confirmed).
- Business Quality `fraud_risk` remains in `unresolved_risk_flags` only, never `active_gates` (confirmed).
- `engine_version_provenance` remains `"adapter_supplied_default"` for Business Quality's defaulted version (confirmed).
- Legacy `growth_score`/`valuation_score` remain unusable as modern engine evidence (confirmed).

## Historical and Live Case-Study Terminology Check

Re-read Sprint #004's report and Epic 004's own closure/sprint records directly. **No ambiguity requiring correction was found**: Sprint #004's report already correctly states `RELCAPITAL` "correctly does NOT [trigger `CP-02`]," distinct from `RELINFRA`/`VEDL`/`GTLINFRA`, which are explicitly described as "live, current" triggers, never presented as forced historical matches. `GTLINFRA`'s inclusion is justified — it was a real, live `CP-02` trigger in Sprint #004's own 274-company run (confirmed again, unchanged, in this sprint's spot-check below), correctly named as such, not removed and not added without evidence. **No documentation correction was required this sprint.**

## Targeted Test Matrix

17 new tests (`tests/regression/test_recommendation_consolidation_structural_coverage.py`):

| Group | Tests | Proves |
|---|---|---|
| `TestStructuralUnavailabilityIsNotAConflict` | 5 | India's structural FS absence no longer fires `CP-07`; produces a coverage notice instead; never opposing evidence; never lowers thesis state; deterministic |
| `TestCompanySpecificUnavailabilityRemainsDistinguishable` | 3 | US's genuine company-specific FS gaps still correctly fire `CP-07`; never produce a coverage notice; never become negative evidence |
| `TestSpecialStatesRemainDistinguishable` | 4 | `not_applicable` ≠ `unavailable` ≠ `execution_error` ≠ `feature_disabled`; `feature_disabled` never produces a coverage notice (confirmed not confused with the structural case) |
| `TestGateAndProvenanceSemanticsPreserved` | 4 | Sprint #004's two corrections remain intact under this sprint's own changes |
| Pre-existing RCI tests (Sprints #003–#004) | 61 | All still pass, unaffected — confirmed the existing `CP-07` fixture test happened to use a genuine `UNAVAILABLE` case (US market), not the structural `NOT_APPLICABLE` case, so no existing test needed correction |

**Full backend suite: 848/848 passing (831 pre-existing + 17 new).**

## Live Spot-Check Results

Per this sprint's own "use Sprint #004's live validation results as a baseline where verifiable, do not rerun expensive network work unnecessarily" instruction: reconstructed `RecommendationEvidenceSnapshot`s directly from Sprint #004's already-captured real evidence (no new network calls), re-run through this sprint's updated consolidation logic.

| | India (60 companies checked) | US (35 companies checked) |
|---|---|---|
| No longer show the false structural `CP-07` | **59/60** | n/a |
| Show a coverage notice instead | **60/60** | n/a |
| Genuine company-specific `CP-07` correctly preserved/absent | n/a | **35/35 (100%)** |

**The one India exception (`BANDHANBNK`, 59/60 not 60/60) is the correct, intended result, not a residual defect**: re-inspection found `BANDHANBNK` genuinely has *two* `UNAVAILABLE` engines (Business Quality and Growth Intelligence both failed for this specific company, a real, company-specific data gap, confirmed unchanged since Epic 003's own original feasibility study named `BANDHANBNK` as a known scraper edge case) — `CP-07` correctly *still* fires for `BANDHANBNK`, for the genuine, separate reason, exactly as the refined logic is supposed to behave. This is direct, live proof the distinction works correctly, not a gap to fix.

Bank/NBFC examples (`HDFCBANK`, `BAJFINANCE`, others from Sprint #004's sample) were included in the 60-company India check — all correctly show the coverage notice, none show a false `CP-07`. A Financial Strength liquidity-distress example (`AAL`) and Business Quality unresolved fraud-risk examples (32 real companies across both markets, per Sprint #004's own count) were re-verified via the dedicated gate/provenance tests above, not a fresh live fetch — no new evidence was needed since Sprint #004 already established these facts and this sprint's changes don't touch that logic.

## Non-Interference Proof

Confirmed via the full, unchanged regression suite: neither `prediction_engine.py` nor `daily_picks.py` references any RCI module (re-confirmed, unchanged test); RCI's pure core remains free of network/env/database imports; legacy `growth_score`/`valuation_score` remain unusable as modern evidence; calling the pure function twice with the same snapshot produces equal responses, including the new `coverage_notices` field.

## Technical Debt

- A `not_applicable` `reason_code` for a future sector-specific (non-market) gate does not yet exist as a real case — the contract already supports adding one without a version bump, named here for whenever a real case arises, not implemented speculatively now.
- `stale_snapshot` remains unexercised by any live code path — unchanged technical debt from every prior sprint, still correctly deferred until a snapshot-storage integration sprint exists.
- `CP-08` (low completeness) still has never fired against real data in either market — unchanged from Sprint #004, not re-tested this sprint since nothing about this sprint's changes touches it.

## Recommendation for the Next Epic 005 Sprint

**C — Defer RCI live integration and address one named prerequisite first: the Daily-Picks structural-evidence-persistence gap, named in Sprint #002's own Discrepancy 2 and unchanged since.**

Justification, by the criteria this sprint's brief itself names:
- **Narrative quality**: now strong — the one real usefulness defect found in Sprint #004 is corrected and verified live; no other narrative-quality issue has been found across two full validation passes.
- **Contract integrity**: strong — both Sprint #004 corrections hold, and this sprint found the existing contract already supported the new distinction without expansion.
- **Coverage semantics**: now correct, the explicit subject of this sprint.
- **India and US behavior**: confirmed consistent and correctly differentiated where they should differ (Financial Strength's market asymmetry) and consistent where they should agree (every other status/pattern).
- **Non-interference evidence**: unchanged, strong, re-confirmed.
- **Readiness of the pure RCI core**: high — but **the pure core's own readiness is not the blocking factor**. Sprint #002's Discrepancy 2 (Daily Picks carries forward none of the four engines' structured outputs) remains entirely unaddressed by Sprints #003–#005, because none of them touched `daily_picks.py` by design. **An Integration Readiness Decision today would have to choose between a live-only Stock-Analysis-page integration (technically ready) and a Daily-Picks integration (blocked on a real, named, unaddressed prerequisite)** — exactly the kind of "named prerequisite" Option C anticipates. Recommending Option A (begin the Integration Readiness Decision now) would force that distinction to be made under sprint-brief time pressure rather than as its own, deliberate decision; recommending Option B (another validation sprint) would not address a real architectural gap with more validation. **Option C is the evidence-correct choice.**
- **Final commit hash**: see below.

---

*No Prediction Engine, Daily Picks, Portfolio, Watchlists, Alerts, Paper Trading, API, persistence, or UI integration was introduced. No score, signal, confidence, threshold, or kill-switch state was changed. No new external data provider was introduced. Validation scripts and raw run output remain outside the committed diff.*
