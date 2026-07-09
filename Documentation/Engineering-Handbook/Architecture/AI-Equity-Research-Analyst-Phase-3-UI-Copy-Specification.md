# AI Equity Research Analyst — Phase 3 UI/Copy Specification

**Document ID:** Epic 008 — Phase 3 Copy/Frontend Contract Specification
**Status:** Copy/contract specification only. No frontend code, TypeScript interfaces, React components, backend code, API payloads, feature flags, or Railway/Vercel configuration were created or modified.
**Governed by:** `CLAUDE.md` → `INDEX.md` → SES-001–005 → [Epic 008B Engineering Contract](AI-Equity-Research-Analyst-Engineering-Contract.md) (frozen) → [Phase 1 Spec](AI-Equity-Research-Analyst-Phase-1-Intelligence-Snapshot-Sprint-Specification.md) → [Phase 2 Spec](AI-Equity-Research-Analyst-Phase-2-Report-Composer-Sprint-Specification.md) → this specification
**Builds on:** Phase 2 (commit `f51223b`) — `research_report` verified present with flag on locally (six sections, no advice/probability language, no mutation) and verified absent with flag off in production
**Direct precedent:** Epic 005 Sprint #010 (UI/Consumer Design) and Sprint #011 (Copy and Frontend Contract Spec) — this document follows their exact discipline: resolve wording and typing before any component code exists; a copy-misattribution is the single highest risk in a feature like this, and it is cheapest to fix before code exists

---

## 1. Evidence Checkpoint

Reviewed directly for this spec: the Phase 1 and Phase 2 specs, the Phase 2 implementation report, `report_assembler.py`'s actual section/entry shape (not summarized from memory), `research_composer.py`'s fail-open/flag behavior, the RCI Sprint #010/#011 UI precedent, the existing `DisclosurePanel.tsx` primitive, and the Stock Detail page's current `EvidenceSummary` placement.

**Facts reconfirmed, non-negotiable for this spec:**

| Fact | Status |
|---|---|
| `research_report` is additive; its absence must never degrade the existing Stock Detail page | Confirmed — Phase 2 composer fails open, omits the key entirely on any failure |
| Six sections, fixed order: `executive_snapshot`, `current_signal_context`, `key_evidence`, `risks_and_invalidation`, `data_availability`, `disclaimer` | Confirmed directly from `report_assembler.SECTION_ORDER` |
| Every section's `entries` items carry `evidence_ids`, `label`, `value`, `provenance_ref`, and most carry `owner`/`display_value` | Confirmed from `report_assembler._entry()` |
| A section may instead carry `text` only (no entries) — the honest-absence path | Confirmed (`current_signal_context.text = "Current decision output unavailable."` when the decision is absent; similarly for `key_evidence`, `risks_and_invalidation`, `data_availability`) |
| `report_contract_version`, `snapshot_id`, `snapshot_hash`, `scope`, `generated_at`, `data_as_of`, `overall_status`, `disclosures` are top-level fields alongside `sections` | Confirmed from `ResearchReport` dataclass |
| `RESEARCH_ANALYST_V2_ENABLED` is off in production; `research_report` is absent from every live response today | Confirmed by the flag-off/flag-on verification tasks immediately preceding this one |
| No LLM exists; every string in the report is a fixed backend template | Confirmed — no narrative renderer exists in this codebase |

**No contradiction found.** This spec designs against the report shape as it is actually implemented, not an aspirational shape.

### Actual report shape, as returned today (flag on, illustrative — not fabricated; matches the verified local response)

```json
{
  "report_contract_version": "1.0",
  "snapshot_id": "live:AAPL:US:short:2026-07-09T07:06:27...",
  "snapshot_hash": "…sha256…",
  "scope": {"symbol": "AAPL", "market": "US", "exchange": "US-composite", "currency": "USD", "horizon": "short"},
  "generated_at": "2026-07-09T07:06:27+00:00",
  "data_as_of": "2026-07-08T00:00:00-04:00",
  "overall_status": "PARTIAL",
  "sections": [
    {"section_id": "executive_snapshot", "title": "Executive Snapshot", "entries": [ /* label/value/evidence_ids */ ], "evidence_ids": [ /* ... */ ], "text": null},
    {"section_id": "current_signal_context", "title": "Current Signal Context", "entries": [ /* signal, confidence, trade levels */ ], "evidence_ids": [ /* ... */ ], "text": null},
    {"section_id": "key_evidence", "title": "Key Evidence", "entries": [ /* per-owner supported items */ ], "evidence_ids": [ /* ... */ ], "text": null},
    {"section_id": "risks_and_invalidation", "title": "Main Risks and Invalidation Conditions", "entries": [ /* bear-case items */ ], "evidence_ids": [ /* ... */ ], "text": "No approved invalidation condition is currently available for this scope."},
    {"section_id": "data_availability", "title": "Data Availability and Limitations", "entries": [ /* non-supported owners */ ], "evidence_ids": [ /* ... */ ], "text": null},
    {"section_id": "disclaimer", "title": "Disclosures", "entries": [ /* disclosure strings */ ], "evidence_ids": [ /* ... */ ], "text": "This report is research and explanation generated from validated platform evidence. It is not financial advice, a guarantee, or an instruction to trade."}
  ],
  "disclosures": ["This is research and explanation, not a guarantee or an instruction to trade."]
}
```

Every section always renders — a section with no citable evidence carries `entries: []` and a non-null `text` (the fixed honest-absence sentence). A UI implementation must render `text` when `entries` is empty and render `entries` when populated; it must never treat an empty-entries section as "hide this section."

---

## 2. Objective

Specify, before any frontend code exists, exactly what a future Phase 3 implementation must display for `research_report`, in what words, in what layout, and how it must behave for every combination of (flag off, report absent on success, report present with full/partial/degraded evidence). This resolves the single highest risk named by both this Epic's own Architecture spec and the RCI precedent: wording that misattributes an existing signal or reads as a second recommendation.

## 3. Non-Goals

No LLM, no chatbot, no conversational UI (all report text is the fixed backend template from Phase 2 — nothing here designs for generated narrative). No Bubble Risk / Bubble Territory (a separate, unrelated future item; not referenced by this report at all). No Daily Picks UI (the report exists only on the live Stock Detail `/predict` response; Daily Picks has no persisted snapshot and is out of scope per Phase 2's own deferral). No Portfolio-aware rendering. No change to the existing `EvidenceSummary` (RCI) component's own behavior or copy. No new backend field, no new API contract, no new snapshot type. No component implementation, no TypeScript interface, no flag enablement — this document is design only.

## 4. Placement on Stock Detail

**Recommendation: directly below the existing `EvidenceSummary` (RCI) block, above the horizon tab bar** — the same slot the RCI UI Consumer Design (Sprint #010) chose for Evidence Summary itself, extended one card further down. Rationale:

- Both blocks share one governing rule (Contract §4.3 / RCI's own precedent): a positive conclusion must never render ahead of its corresponding risk/limitation. Stacking Research Summary immediately after Evidence Summary keeps both "explain, don't decide" blocks adjacent and visually distinct from the decision-bearing header/signal card above them.
- The existing signal/confidence/target card, the technical indicators, and the horizon tabs remain completely undisturbed — Research Summary is an appendix to the page, never a replacement for or reordering of existing decision UI (Consumer Mapping Contract §10.1: "replacing existing signal, confidence, target, or stop-loss displays" is forbidden).
- It reuses the same `DisclosurePanel` primitive already proven for Evidence Summary — no new expand/collapse component is required (confirmed: `DisclosurePanel.tsx` is generic, not RCI-specific).

## 5. Label

**Card title: "AI Research Summary."** Rejected alternatives and why, mirroring the RCI Sprint #010 naming exercise:

| Rejected | Reason |
|---|---|
| "Investment Thesis" | Reads as a conclusion StockSense360 is asserting, not a summary of evidence already shown elsewhere on the page. |
| "Research Explanation" | Accurate but generic; doesn't signal to the user this is a distinct, labeled AI-assembled feature (transparency requirement — Contract §7.1 rule 1: name what's being shown). |
| "AI Recommendation" / "AI Verdict" | Explicitly forbidden — reads as a second, competing recommendation to the Prediction Engine's own signal (Contract §2 invariant 4: no alternate BUY/HOLD/SELL recommendation). |

**Subtitle (always shown, fixed):** "A structured summary of evidence already computed by StockSense360's engines — not a new recommendation." This sentence itself is a compliance-load-bearing line, not filler; it must ship unchanged in every rendering including the collapsed/absent states below.

## 6. Displaying the Six Sections Safely

One `DisclosurePanel` per section (all closed by default except `current_signal_context`, which is Contract-mandated as the report's central visible fact and should render always-expanded — the same "never collapse the decision context" logic RCI applies to `active_gates`). Section-by-section rendering rule, driven entirely by backend-provided `text`/`entries` — the frontend derives no state from raw values:

| Section | UI heading | Render rule |
|---|---|---|
| `executive_snapshot` | "At a Glance" | Always has entries (scope/timestamps/status are validator-guaranteed present). Render as a compact key-value strip: scope line, generated-at, data-as-of, snapshot status badge, evidence-state counts as a small inline tally (e.g. "6 supported · 2 unavailable · 1 not applicable") — counts rendered as counts, never as a percentage or score. |
| `current_signal_context` | "Current Signal (from Prediction Engine)" | If `entries` present: render signal/confidence/trade-level values labeled "as computed by the Prediction Engine" — verbatim, no restyling of the signal badge beyond what the existing signal display already uses elsewhere on the page (reuse, don't reinvent). If `entries` empty: render `text` ("Current decision output unavailable.") in the same visual register as any other empty state on the page — not an error banner. |
| `key_evidence` | "Supporting Evidence by Engine" | Group entries by their `owner` field (already present per entry) into labeled sub-lists (Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence, Legacy Quality Factor — labeled exactly "Quality Factor (legacy)", never "Business Quality," per Phase 1's own owner-naming discipline, Market Regime, Global Context). If `entries` empty: render `text` verbatim. |
| `risks_and_invalidation` | "Risks and What Could Change" | Render risk entries as a plain bulleted list (no severity color-coding invented by the frontend — the backend supplies no severity field for these items, unlike RCI's `material_warnings`). Always render `text` below the list (it is non-null in every case — either the risk list is present with the fixed invalidation-unavailable sentence, or both are absent and a combined sentence renders). Never let this section collapse by default if `entries` is non-empty (mirrors the "never hide risk ahead of a positive conclusion" rule). |
| `data_availability` | "Data Coverage and Limitations" | Render each entry's `label` (already formatted `"{Owner}: {STATE}"`, e.g. `"FinancialStrength: NOT_APPLICABLE"`) with its `value` (the owner's reason) beneath it, verbatim. **The frontend must not re-map or soften these state words** — `NOT_APPLICABLE` and `UNAVAILABLE` must render as visually and textually distinct (e.g., "not applicable to this market" vs. "temporarily unavailable"), never merged into one generic "no data" phrase (Contract §8.2, the exact discipline RCI's own Sprint #005 proved matters). If `entries` empty: render `text` ("All registered evidence owners supplied evidence for this scope.") as a positive coverage note, not hidden. |
| `disclaimer` | "Disclosures" | Always rendered, always expanded, never collapsible — mirrors the RCI rule that the user-decision boundary can never be tucked behind an interaction. Render `text` plus each `entries[].value` disclosure string verbatim. |

## 7. Citing Evidence / Provenance

Every entry the backend emits carries `evidence_ids` (≥1 item) and, where applicable, `owner`/`provenance_ref`. Phase 3 UI rule: **render a small superscript or trailing badge per entry showing its owner** (already labeled — no frontend inference needed), and make `evidence_ids` available via a `title`/tooltip attribute (`evidence_ids.join(", ")`) for inspection, mirroring RCI's own "make it possible to inspect, at minimum, named evidence owners and selected evidence items" requirement (Contract §7.4). **No entry may be rendered without its owner label visible** — an unattributed claim is exactly the failure mode both this Contract and RCI's copy spec name as prohibited ("hiding the evidence ledger behind untraceable prose").

A future, explicitly deferred enhancement (not this phase): a click-to-expand raw evidence-item viewer. Phase 3 needs only visible owner attribution, not a full drill-down UI.

## 8. Showing Missing/Unavailable Evidence Honestly

Directly inherited from RCI Sprint #011's own precedent, applied to the five non-`SUPPORTED` Phase 1 states surfaced in `data_availability`:

| Backend state (verbatim in entry label) | Required frontend treatment | Forbidden treatment |
|---|---|---|
| `MISSING` | "This data was not present in this evaluation." | Never imply a negative signal. |
| `UNAVAILABLE` | "This data could not be retrieved for this evaluation." | Never say "failed" or expose the raw owner reason as an error. |
| `NOT_APPLICABLE` | "This evidence category does not apply to this market/instrument." | Never present as a gap or a red flag — it is a structural fact (e.g., Financial Strength has no India implementation), exactly RCI's own `NOT_APPLICABLE`-vs-`UNAVAILABLE` distinction. |
| `FEATURE_DISABLED` | "This evidence source is currently disabled; its absence is not a negative result." | Never count toward any visual "coverage score" — there is no coverage score to begin with (Contract §8.1). |
| `EXECUTION_ERROR` | "This data could not be computed for this evaluation." | Never surface the underlying exception text — Phase 2's own composer already strips it; the frontend must not re-introduce detail via a different channel (e.g., a browser console log of the raw payload is acceptable for developers, but the rendered UI text stays bounded). |

The `executive_snapshot` state-count tally (§6) is the only place counts appear, and it counts occurrences of these exact words — never converts them into a percentage, star rating, or "data quality score."

## 9. Avoiding Advice Language

The backend (`report_assembler.FORBIDDEN_ADVICE_TOKENS`) already guarantees no advice phrasing exists in any string it emits, and Phase 2's test suite scans every generated report against that list. **The frontend's own obligation is narrower but still real: never add advice language of its own** — no computed call-to-action button ("Buy AAPL now"), no color-coded urgency treatment on `current_signal_context` beyond the page's existing (pre-Phase-3) signal styling, no numeric "conviction meter" invented from evidence counts, and no auto-generated summary sentence composed client-side from section contents (that would be exactly the kind of client-side synthesis this Epic's every phase has prohibited). The card renders backend strings and backend-labeled values; it composes no new sentence.

## 10. Handling Flag OFF / Report Absent

**Two structurally identical cases, one rendering rule:** whether the flag is off (key never attempted) or the flag is on but composition failed (key omitted by design — Phase 2's fail-open Option A), the frontend sees the exact same thing: **no `research_report` key on the prediction response.** Required behavior:

- **Render nothing** — no card, no placeholder, no "AI Research Summary is currently unavailable" banner, no loading spinner that never resolves. The existing `getValidRecommendationConsolidation()`-style pattern in `frontend/src/utils/api.ts` (a single absence/malformed-value decision point returning `null`) is the exact model: a future `getValidResearchReport(prediction)` helper returns `null` on any missing/malformed shape, and the Stock Detail page renders `{report && <ResearchSummary report={report} />}` — nothing else.
- **This must never be visually distinguishable from "the feature doesn't exist yet"** — Contract §10.1's own rule ("existing product behavior remains independently correct... treating delayed or missing Analyst content as a failure of the underlying stock page" is forbidden) applies identically whether the cause is the flag or a transient composition failure. The user never sees an error state for this feature; they simply don't see the card.
- **No polling, no retry, no "check again" affordance** — the report either arrived with the prediction response or it didn't; Phase 3 introduces no new network request.

## 11. Explicitly Out of Scope for Phase 3 Design

No frontend implementation, no backend implementation, no TypeScript contract authored (a future implementation phase must still write `ResearchReport`/`ReportSection` interfaces per this spec's field names before any component work, mirroring RCI Sprint #011's own "typing is complete, only copy/UX review remains" gate). No flag enablement anywhere. No LLM/chatbot design (008C+ remains separately gated). No Bubble Risk/Bubble Territory reference of any kind — that is a distinct, unrelated future item and must not be introduced into Research Analyst copy. No Daily Picks or Portfolio rendering. No new backend field (e.g., a `reason_code` on report entries) — if a future implementation finds a genuine typing gap analogous to RCI Sprint #011's `coverage_notices` discrepancy, it must be named as an explicit backend-contract prerequisite, not silently worked around client-side, exactly as RCI's own precedent handled it.

## 12. Recommendation

Per the RCI Sprint #010/#011 precedent this document mirrors: **the next step is a frontend-only implementation sprint** (TypeScript contract + `ResearchSummary` component reusing `DisclosurePanel`, wired into Stock Detail exactly per §4, flag left disabled throughout implementation, verified via `tsc`/build/manual local-server check since no frontend test framework exists — the same confirmed gap RCI Sprint #012 worked around). That implementation sprint should treat this document's copy and placement decisions as fixed inputs, the same way RCI Sprint #012 treated Sprint #011's copy catalogue as ready-to-implement.
