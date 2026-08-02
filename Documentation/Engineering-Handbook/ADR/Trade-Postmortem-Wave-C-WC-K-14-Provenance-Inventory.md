# Wave C, WC-K-14 — Persisted Version Provenance Inventory

- **Status:** WC-K-14 PARTIAL — see verdict at the bottom.
- **Scope:** every version/provenance-shaped value the current-report
  read API (`GET /api/paper-trading/{trade_id}/current-report`) could
  expose, mapped to its actual persisted source, based on reading
  `report_store.py`, `generation_service.py`, and
  `price_path_generation.py` directly — not assumed from the frozen
  Wave C prompt's hypothetical field list.

| # | Provenance value | Persisted source | Required/Optional | Response field | Behaviour when absent |
|---|---|---|---|---|---|
| 1 | Report schema version | `paper_trade_postmortem_report.report_schema_version` column | Required (NOT NULL) | `report_schema_version` | N/A — column is NOT NULL |
| 2 | Calculation version | `.calculation_version` column | Required (NOT NULL) | `calculation_version` | N/A |
| 3 | Governed semantic/price-path + claim-rules version | `.attribution_rules_version` column (single column carries both — Wave B's `current_target_identity()` derives it from `GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION`, but the RESPONSE reads the persisted column, not the constant) | Required (NOT NULL) | `attribution_rules_version` | N/A |
| 4 | Evidence-bundle version | `.evidence_bundle_version` column | Required (NOT NULL) | `evidence_bundle_version` | N/A — closed this turn, adversarial PG proof added |
| 5 | `source_manifest.exit_snapshot_schema_version` | nested in `.source_manifest` JSONB, set in `generation_service.py:176` | Optional — `None` when no exit snapshot exists | not currently surfaced as its own field; available inside `source_manifest` | Persisted as JSON `null`; endpoint returns it unchanged inside `source_manifest` |
| 6 | `source_manifest.exit_evidence_rules_version` | nested, `generation_service.py:178` (`EXIT_EVIDENCE_RULES_VERSION` constant, persisted at generation time — a historical row keeps whatever value was current when it was generated, never re-derived) | Required within `source_manifest` for every Sprint-2-or-later report | inside `source_manifest` | N/A |
| 7 | `source_manifest.phase1_calculation_version` | nested, `generation_service.py:179` | Required | inside `source_manifest` | N/A |
| 8 | `source_manifest.attribution_rules_version` (Sprint-2-era, pre-price-path) | nested, `generation_service.py:180` | Required for Sprint-2/1.0.0 rows | inside `source_manifest` | N/A |
| 9 | `source_manifest.price_path_calculation_version` | nested, added only when a price-path enhancement is applied (`price_path_generation.py:349`, via `price_path_calculation_suffix(source_version)`) | **Historical-optional** — absent on any report that predates or never received price-path enhancement (e.g. a plain Sprint 2 1.0.0/1.1.0 row) | inside `source_manifest` | Genuinely absent on those rows; the endpoint must not synthesize it |
| 10 | "Source ID" / "source-manifest schema version" / "provider-library version" / "boundary-policy version" (as named in the Wave C master prompt) | **Not found anywhere in the actual persisted contract** — no such keys are ever written by `generation_service.py` or `price_path_generation.py` | **Missing contract requirement** — these are not currently part of the Wave B/Wave C frozen persistence contract | N/A | The endpoint correctly returns whatever `source_manifest` JSON actually contains (which never has these keys today) rather than fabricating them |
| 11 | Claim-level rule/version metadata | Each claim dict (`evidence.py`'s rule registry) carries its own `rule_id`/`rule_version`-shaped fields at claim-construction time, persisted verbatim inside the `claims` JSONB array — not a separate top-level column | Present per-claim, not per-report | inside `claims[]` | Endpoint returns `claims` unchanged; no separate top-level provenance field exists for this today |
| 12 | Supersession provenance | `.supersedes_report_id` column (BIGINT, nullable) | Optional — NULL unless this report supersedes an older one (Stage I price-path enhancement) | `supersedes_report_id` | Already correctly nullable in the response |

## Verdict

Items 1–4 and 12 are directly-persisted top-level columns, already
correctly sourced from the exact `PersistedReport` row (never from a
current code constant) — item 4 has adversarial PostgreSQL proof as of
this Wave C pass. Items 5–9 are nested inside the existing
`source_manifest` JSON blob, which the endpoint already returns
verbatim (unmodified, unfabricated) as part of `structured_report`'s
sibling field — no separate typed extraction has been implemented for
these nested values, which the frozen typed-response-contract work
(§6 of the governing prompt) still needs to address explicitly rather
than leaving them opaque inside an untyped `dict`. Item 9 is
historical-optional and must never be synthesized for a pre-price-path
row. Item 10 (source ID / source-manifest schema version /
provider-library version / boundary-policy version) does not exist in
the actual persisted contract at all — no code path in this repository
ever writes such keys — so no test or implementation work can honestly
close it; it is recorded here as a **missing contract requirement**,
not an implementation gap, and any future closure would require first
deciding (as an owner/product decision, not an engineering default)
whether the persistence layer should start writing these fields for
newly generated reports.

**WC-K-14 remains PARTIAL**: the top-level column provenance (items
1–4, 12) is fully closed with real PostgreSQL evidence. The nested
nested `source_manifest` nested-field typing (items 5–9) and the
non-existent items 10 require, respectively, typed-model work (§6) and
an explicit owner decision before they can be closed — this file does
not resolve them itself.
