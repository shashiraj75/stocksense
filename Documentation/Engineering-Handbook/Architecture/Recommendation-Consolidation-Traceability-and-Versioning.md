# Recommendation Consolidation Intelligence — Traceability and Versioning (Epic 005, Sprint #002)

**Status:** Contract-design sprint only. No production code modified. Companion to [Recommendation Consolidation Evidence Contract](Recommendation-Consolidation-Evidence-Contract.md) — that document defines *what* evidence RCI consumes and returns; this one defines *how* every output stays traceable across live and historical contexts, and how the contract evolves without breaking anything.

## 9. Live vs. Snapshot Traceability Contract

### Live analysis (Stock Analysis page)

Carries, per this sprint's specification:
- Current run timestamp (`predict()`'s own existing `generated_at` — confirmed already present, reused not duplicated).
- Current engine versions (`metadata.engine_version` per engine — present on three of four today; Business Quality's absence is Discrepancy 1, named in the companion Evidence Contract, to be closed in Sprint #003).
- Current data timestamps (implicit via the underlying provider fetch — confirmed already governed by each provider's existing cache TTLs, e.g. screener.in's 4-hour cache).
- Current gate and feature-flag state (`_valuation_intelligence_confidence_enabled`/`_growth_intelligence_confidence_enabled`'s live values at call time).
- Current evidence-snapshot identifier — a **new** concept: a generated ID tying one `RecommendationEvidenceSnapshot` (Evidence Contract §3) to one specific `predict()` invocation, for audit purposes.

### Daily Picks snapshot

**Confirmed via direct code reading, not assumed: this does not yet exist for the four new engines.** The existing `_write_score_snapshots`/`log_score_snapshot` mechanism stores `growth_score`/`valuation_score` fields sourced from `quality_factors.py`'s legacy `breakdown.earnings_revision`/`breakdown.valuation` — **not** from Growth Intelligence or Valuation Intelligence at all (Discrepancy 2, Evidence Contract). The existing Daily Picks row dict (`_predict_stock()`'s return value) carries forward only the already-blended `confidence` and free-text `reasoning` — never the four engines' structured outputs.

**What a future Daily Pick snapshot must carry, once that prerequisite work happens (a separately-scoped future sprint, not this one):**
- Generation timestamp (already exists, `generated_at`, confirmed in the existing picks-cache structure).
- Original signal, original confidence (already exist, confirmed).
- Original supporting/opposing evidence — **does not exist today**; requires the row dict to carry forward each engine's structured status, per the Evidence Contract's §4 field table.
- Original gates and warnings — **partially exists** (free-text `reasoning` entries are stored, but not as structured, machine-readable gate flags).
- Original engine versions — **does not exist today** (compounds Discrepancy 1 — even once Business Quality gets an `engine_version` field, nothing currently *persists* any engine's version alongside a Daily Pick).
- Original consolidation-contract version — **a new field, does not exist, cannot exist until RCI itself is implemented.**
- Original target, entry zone, stop-loss — **already exist and are already correctly frozen at generation time** (confirmed: `_predict_stock()`'s row dict captures `target`, `entry_low`, `entry_high`, `stop_loss` directly from `predict()`'s own output at generation time, unchanged by anything this sprint touches).
- Current live comparison, if later displayed — **a new UI/data concern**, explicitly deferred (no UI implementation this sprint or in RCI's V1 scope at all, per SSDS-009).

**The non-negotiable rule, confirmed and re-stated, not weakened:** a Daily Pick must retain its historical conclusion even when live analysis later differs. **The system must never silently overwrite a historical recommendation with current evidence.** This is already true today for `target`/`confidence`/`signal` (confirmed, the existing snapshot mechanism already freezes these correctly) — RCI's own future fields must extend this same guarantee, not introduce a new, weaker one.

## 10. Versioning, Auditability, and Backward Compatibility

- **Contract versioning strategy:** a simple, monotonically-increasing integer `contract_version` (e.g. `1`, `2`, ...) stamped onto every `RecommendationConsolidationResponse` at the moment it is computed — mirroring the same "small, explicit version tag" pattern `engine_version: "v1"` already establishes for individual engines (Financial Strength, Growth Intelligence, Valuation Intelligence), extended to the consolidation layer itself.
- **Engine-version capture:** every engine's own `metadata.engine_version` (once Discrepancy 1 is closed for Business Quality) is read and stored verbatim into `engine_versions_used` (Evidence Contract §8) at the moment RCI runs — never inferred, never assumed current.
- **Backward-compatible field additions:** new fields may be added to a later contract version without bumping `contract_version` if and only if they are purely additive (a consumer ignorant of the new field sees no behavior change) — a breaking change (renaming/removing/changing the meaning of an existing field) requires a new `contract_version`, never a silent redefinition.
- **Unknown fields:** any consumer (future UI, future Risk Intelligence) encountering a field it doesn't recognize must ignore it gracefully, never error — the same "graceful degradation, never fabricate certainty" principle already governing every existing engine's missing-data handling, extended to the contract's own evolution.
- **Missing historical fields:** a Daily Pick captured before a given contract field existed must show that field as `not_recorded` (a distinct value from `unavailable`/`not_applicable` — this one specifically means "the contract didn't capture this yet," not "the evidence didn't exist"), never backfilled with a guess.
- **Stale snapshots:** any Daily Pick whose stored `contract_version` is older than the current one is tagged `status: stale_snapshot` (Evidence Contract §5) when displayed — its original conclusion is still shown and still authoritative for *that historical moment*, but a live re-analysis is visibly offered as a distinct, separate view, never silently blended with the old one.
- **Future Risk Intelligence joining the contract:** the contract's per-engine evidence list (Evidence Contract §4) is already designed as an open list, not a fixed four-tuple — a fifth engine (Risk Intelligence, once it exists) would add one more entry to `supporting_evidence`/`opposing_evidence` and one more conflict-pattern candidate (§7), without requiring any existing field's shape to change. This is a direct, intentional consequence of never hard-coding "exactly four engines" anywhere in the output contract's field definitions.
- **Future UI citing evidence safely:** because every output field (Evidence Contract §8) is either a categorical label, a list of already-existing engine names/statuses, or a direct timestamp/version string, a future UI can render any of them directly without needing to interpret a blended number or reverse-engineer a weight — the explainability-first design constraint is what makes "safe to cite" true by construction, not an added UI-layer responsibility.

**The contract is extensible by design (open evidence list, additive versioning) while V1 itself remains deliberately narrow** — only the fields in the Evidence Contract's §4 "must-have" row, nothing speculative.

---

*No production code, data class, or API was implemented this sprint. Companion: [Recommendation Consolidation Evidence Contract](Recommendation-Consolidation-Evidence-Contract.md).*
