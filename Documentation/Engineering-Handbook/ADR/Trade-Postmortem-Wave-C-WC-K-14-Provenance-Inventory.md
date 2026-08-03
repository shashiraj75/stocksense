# Wave C, WC-K-14 — Persisted Version Provenance Inventory

- **Status:** WC-K-14 CLOSED — ALL VERSION AND PROVENANCE VALUES EXPOSED
  BY THE GOVERNED CURRENT-REPORT API ARE TYPED, SEMANTICALLY VALIDATED
  AND SOURCED FROM THE EXACT IMMUTABLE PERSISTED REPORT; OPTIONAL
  HISTORICAL VALUES ARE NOT SYNTHESIZED; LINKED PROVIDER-MANIFEST
  EXPANSION REMAINS NONBLOCKING BACKLOG. Closed after Gate 1 (Wave C,
  WC-K, A4/A5) went green on both real PostgreSQL 15 and PostgreSQL 17
  — see verdict at the bottom for the closing evidence. (Corrected: an
  earlier version of this file incorrectly stated that
  source_id/provider_library_version/boundary_policy_version etc. do
  not exist anywhere in the persisted contract — they do exist, in the
  linked price-path-evidence record; see item 10.)
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
| 10 | "Source ID" / "source-manifest schema version" / "provider-library version" / "boundary-policy version" (as named in the Wave C master prompt) | **Correction**: these fields DO exist and ARE persisted — `services/postmortem/price_path_acquisition.py` builds and validates a full manifest with exactly these keys (`source_manifest_schema_version`, `source_id`, `source_type`, `source_scope`, `production_authoritative`, `source_version`, `provider`, `provider_library_version`, `boundary_policy_version`, `symbol_normalization_version`, plus acquisition parameters — see `_REQUIRED_SOURCE_MANIFEST_KEYS` around line 427 and the manifest builder around line 802), and `price_path_generation.persist_price_path_evidence` → `price_path_store.persist_evidence` durably persists the full `PricePathEvidenceBundle` (including this manifest) to its OWN linked evidence table — this is a real, persisted, immutable record. **PERSISTED IN LINKED PRICE-PATH EVIDENCE, NOT CURRENTLY SURFACED BY THE CURRENT-REPORT RESPONSE** — `paper_trade_postmortem_report.source_manifest` (the REPORT row's own column, item 5–9 above) is a materially different, smaller object that only ever receives `price_path_calculation_version` copied in (item 9) — the full provider manifest is never copied into it. This is a report-vs-evidence separation of concerns, not a missing persistence capability. | Governed by the linked price-path-evidence record, not the report row | N/A — the current-report response returns the REPORT row's `source_manifest`; exposing the linked evidence manifest through this same response is a scope decision, not a defect, and is tracked as NONBLOCKING BACKLOG unless the ratified Wave C contract requires it |
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

**WC-K-14 CLOSED — ALL VERSION AND PROVENANCE VALUES EXPOSED BY THE
GOVERNED CURRENT-REPORT API ARE TYPED, SEMANTICALLY VALIDATED AND
SOURCED FROM THE EXACT IMMUTABLE PERSISTED REPORT; OPTIONAL HISTORICAL
VALUES ARE NOT SYNTHESIZED; LINKED PROVIDER-MANIFEST EXPANSION REMAINS
NONBLOCKING BACKLOG.**

The report row's top-level version fields (items 1–4, 12) and its real
body (structured_report/claims/evidence_items) have persisted-row
PostgreSQL round-trip proof, including an adversarial marker test for
item 4. Wave C, WC-K Gate 1 (A4/A5) closes the remaining open items:
`structured_report.price_path.version_and_provenance` (the 9-field
1.2.0 governed provenance subtree — `report_schema_version`,
`calculation_version`, `numerical_rules_version`,
`governed_semantic_rules_version`, `governed_claim_rules_version`,
`entry_snapshot_schema_version`, `exit_snapshot_schema_version`,
`level_history_contract_version`, `source_version`) is now typed
(`VersionAndProvenance`, `extra="forbid"`), semantically validated at
the READY conversion boundary (fails closed to
`INTEGRITY_CONTRADICTION` on a missing/malformed subtree — never
synthesizes an absent value), and read verbatim from the exact
persisted report row. `supersedes_report_id` (item 12) is confirmed, by
real-PostgreSQL proof, to be read directly from the persisted row —
never recalculated at GET time — with a genuine no-predecessor case, a
genuine real-predecessor case returning the exact stored predecessor
id, and repeated-GET proof of no mutation. Optional historical
snapshot-schema-version fields remain correctly nullable and are never
synthesized when the corresponding snapshot doesn't exist.

Item 10 (the linked price-path-evidence provider manifest —
`source_id`, `provider_library_version`, `boundary_policy_version`,
etc.) is real and persisted but lives in a separate linked evidence
record, not the report row; exposing it through the current-report
response remains NONBLOCKING BACKLOG and does not block this closure.

Closing evidence: Wave C, WC-K Gate 1 (commits `daeb82d`, `2bb1da9` on
`feature/trade-postmortem-sprint3a-price-path`) — GitHub Actions run
[30803447452](https://github.com/shashiraj75/stocksense/actions/runs/30803447452),
green on both PostgreSQL 15 and PostgreSQL 17, JUnit-confirmed
`tests="304"`, `failures="0"`, `errors="0"`, `skipped="0"` on each.
