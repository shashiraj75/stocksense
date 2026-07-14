# Phase 1A.6 — Market Integrity Hardening and Historical Repair Planning

## 1. Why this phase exists

Commit `536fd3d` (2026-07-12) fixed a specific defect: `PredictionEngine._composite_signal()` had a resolved `market` value in scope but never forwarded it to `log_prediction()`, which silently fell through to that function's old `market="IN"` default. This mislabeled a population of US predictions as `market='IN'` over several weeks before the gap was noticed (the "Phase 1A.5" forensic investigation quantified the scope of this legacy population; see that phase's own report for the exact historical figures — this document does not reproduce them).

That commit closed the one call site that triggered the defect, but the API boundary itself still allowed `market` to default silently, and nothing at the write boundary ever checked whether a symbol could plausibly belong to the claimed market at all. Phase 1A.6 closes both of those gaps, adds containment to the outcome resolver/manifest pipeline and the two production metric paths that read the `predictions` table, and builds a dry-run-only historical repair planner. This phase does **not** execute any repair, backfill, or relabeling — see §20.

**Two remediation passes.** The first pass hardened `log_prediction` only. A subsequent forensic diff review found that `log_outcome` — the sibling write boundary for the `outcomes` table — still carried the exact same silent-default defect, and that `scripts/migrate_sqlite_to_postgres.py` actively exercised it. The second, current pass closes that gap; every claim in this document reflects the state **after** both passes.

## 2. Explicit-market persistence invariant

Every writer of the `predictions` **and `outcomes`** tables must supply an explicit, validated `market` value. There is no persistence code path — application-level or database-level (see §9) — that infers a market when one is missing. (Read-only query functions with their own `market: str = "IN"` default, such as `get_training_data`/`get_unresolved_predictions`/`get_ambiguous_pending_predictions`/`count_training_rows`, are a **separate, lower-risk category** — a default there only changes what a caller queries *for*, never what gets written — see the explicit distinction in §9.)

`log_prediction()` and `log_outcome()` (both in `services/postgres_store.py` and `services/alpha_engine/store.py`) declare:

```python
def log_prediction(..., *, market=MISSING_MARKET, _writer_source: str = "unknown", ...):
def log_outcome(..., *, market=MISSING_MARKET, _writer_source: str = "unknown"):
```

`MISSING_MARKET` (`services/market_integrity.py`) is a dedicated sentinel object, not a valid market string. This is a deliberate design choice over a bare `*, market: str` (required, keyword-only, no default): Python raises `TypeError` for an omitted keyword-only argument **before the function body ever executes**, so no structured event could ever be emitted for that case, and a plain `ValueError` from a normalization helper is never actually caught by an `except MarketConflictError` block (a distinct subclass) either. The sentinel default makes omission a *value* this module's own code can see, log, and reject on its own terms — every failure path below is guaranteed to run and emit its event before raising.

## 3. Exception hierarchy

All defined in `services/market_integrity.py`:

```
MarketIntegrityError                (base)
├── MissingMarketContextError       market was never supplied (sentinel or None)
├── InvalidMarketError              market supplied but blank/unsupported/malformed
└── MarketSymbolConflictError       market definitively contradicts the symbol's classification
```

None of these is raised as a bare `ValueError`/`TypeError` once execution reaches `require_explicit_market()` — every one carries `symbol`, `writer_context`, and (for `MarketSymbolConflictError`) `market`/`classification` as attributes.

## 4. Structured event and reason-code contract

Every event below is a stable string constant in `services/market_integrity.py`, emitted via the shared `emit_market_event()` helper, which sets discrete `extra` fields on the Python `LogRecord` (not just a formatted message): `event`, `reason_code`, `writer_context`, `market`, `raw_symbol`, `normalized_symbol`, `classification`, `prediction_id`, plus call-site-specific fields (e.g. `excluded_count`, `eligible_count`, `requested_count`, `actual_count`). Tests assert against these record attributes directly (`caplog.records[i].event`, `...reason_code`, etc.), not against message substrings.

| Event | Reason code | Emitted from |
|---|---|---|
| `prediction_market_context_missing` | `market_context_missing` | `require_explicit_market` |
| `prediction_market_invalid` | `market_value_invalid` | `require_explicit_market` |
| `prediction_market_symbol_conflict` | `definitive_market_symbol_conflict` | `require_explicit_market` |
| `prediction_symbol_classification_unknown` | `symbol_classification_unknown` | `require_explicit_market` |
| `resolver_market_conflict_skipped` | `definitive_market_symbol_conflict` | `outcome_logger.resolve_pair` |
| `manifest_market_conflict_skipped` | `definitive_market_symbol_conflict` | `manifest_backfill.build_manifest` |
| `manifest_candidate_population_exhausted` | `source_population_exhausted_underfilled` | `manifest_backfill.build_manifest` |
| `manifest_generation_refused` | `zero_usable_candidates` | `manifest_backfill.build_manifest` |
| `performance_market_conflicts_excluded` | `definitive_market_symbol_conflict` | `get_daily_picks_performance`, `get_training_data` (both backends) |

A human-readable message accompanies every event; no event ever logs credentials, URLs, connection strings, or a full row payload.

## 5. Shared classifier and normalization rules

`services/market_integrity.py` is the single choke point every writer, resolver, manifest path, planner, and performance filter imports from — verified by direct grep of every `from services.market_integrity import` site in the repository; no parallel/duplicate classification logic exists anywhere else.

Classification is a two-step, deterministic, network-free process (`classify_symbol_detailed`):

1. Normalize (uppercase, strip whitespace, strip a known `.NS`/`.BO` exchange suffix) and look up the result directly against `stock_universe.IN_SYMBOLS`/`US_SYMBOLS`. Direct match always wins.
2. Only if step 1 is `UNKNOWN` **and** the normalized symbol contains a `.`, try the documented class-share alias transform (§7) and look that up. The alias form is accepted only if it is actually present in a canonical universe.

## 6. IN_ONLY / US_ONLY / BOTH / UNKNOWN and the conflict matrix

| Classification | Meaning | Conflicts with `market=IN`? | Conflicts with `market=US`? |
|---|---|---|---|
| `IN_ONLY` | in `IN_SYMBOLS` only | No | **Yes — MarketSymbolConflictError** |
| `US_ONLY` | in `US_SYMBOLS` only | **Yes — MarketSymbolConflictError** | No |
| `BOTH` | in both sets (genuinely dual-listed, e.g. INFY, HAL, IEX) | No | No |
| `UNKNOWN` | in neither set | No (never a conflict) | No (never a conflict) |

`UNKNOWN` is logged as an observable event (`prediction_symbol_classification_unknown`) and persisted as claimed — never silently defaulted, never rejected. Many legitimate symbols are absent from the curated static universe (newly listed names, etc.) and must not be treated as invalid on that basis alone.

## 7. Class-share alias handling

The canonical universe stores US class-share tickers hyphenated (`BRK-A`, `BRK-B`), but some data providers format them with a dot (`BRK.A`). A prior version of the classifier misclassified `BRK.A` as `UNKNOWN` (confirmed live: `classify_symbol("BRK.A")` → `UNKNOWN`, `classify_symbol("BRK-A")` → `US_ONLY`).

`classify_symbol_detailed` now tries a narrow, documented fallback — replace only the *final* `.` with `-` — but **only** when the direct (untransformed) lookup is `UNKNOWN`, and **only** accepts the transformed form if it is itself present in a canonical universe. An arbitrary dotted symbol whose alias form is also unknown is left as `UNKNOWN`; this never rewrites a symbol that doesn't resolve to a real class-share alias. The result (`SymbolClassification`) preserves the raw input, the canonical (post-transform) symbol, and which `normalization_rule` produced the classification (`"none"`, `"suffix_stripped"`, or `"class_share_alias"`).

## 8. Every prediction and outcome write boundary

Confirmed exhaustively via `grep -rn "INSERT INTO predictions"` and `grep -rn "INSERT INTO outcomes"` across the whole repository:

| File:line | Role |
|---|---|
| `services/postgres_store.py` (`log_prediction`) | Production writer, behind `require_explicit_market` |
| `services/alpha_engine/store.py` (`log_prediction`) | Production writer (SQLite dev path), behind `require_explicit_market` |
| `services/postgres_store.py` (`log_outcome`) | Production writer, behind `require_explicit_market` |
| `services/alpha_engine/store.py` (`log_outcome`) | Production writer (SQLite dev path), behind `require_explicit_market` |
| `tests/regression/test_manifest_backfill.py`, `test_outcome_lifecycle_repair.py`, `test_market_integrity_hardening.py` | Test-only raw-SQL fixtures that deliberately bypass the classifier to seed pre-existing contaminated rows for test setup — never a production code path |

`scripts/migrate_sqlite_to_postgres.py`, `services/daily_picks.py`, `services/prediction_engine.py`, and `services/alpha_engine/outcome_logger.py` (the live 6-hourly resolver) all call `log_prediction()`/`log_outcome()` rather than inserting directly, so they are covered by the same choke point. `execute_outcome_writes_transactional` (the manifest-execution write path) is separately safe: it requires `market` as a mandatory dict key with no fallback, sourced from the manifest's own candidate data.

**Historical note**: `scripts/migrate_sqlite_to_postgres.py`'s `log_outcome` call previously omitted `market` entirely, despite the source row having the column — the exact same defect shape 536fd3d fixed for `log_prediction`, on the sibling table. Found by a forensic diff review of the first remediation pass (which hardened `log_prediction` only) and fixed in the same pass that produced this document's current revision: the call now passes `market=o["market"]` explicitly.

## 9. Database-default removal and the unexecuted migration

The live schema previously carried `market TEXT NOT NULL DEFAULT 'IN'` on both `predictions` and `outcomes`. This phase:

- Removed `DEFAULT 'IN'` from the fresh-schema `CREATE TABLE`/`ADD COLUMN IF NOT EXISTS` statements in both `services/postgres_store.py`'s `SCHEMA_SQL` and `services/alpha_engine/store.py`'s `init_db()` — safe because these only ever execute against a column that doesn't exist yet (an empty table, or a guarded `IF NOT EXISTS`/`try/except OperationalError` no-op against an already-migrated one).
- Left the SQLite `ALTER TABLE ... ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'` legacy fallback (for a genuinely ancient, already-populated SQLite file predating the `market` column) with its default intact — SQLite requires a default to add a `NOT NULL` column to a non-empty table, and this path never fires against any database created by the fresh schema above.
- Added `backend/scripts/migrations/phase_1a6_drop_predictions_market_default.sql`, containing `ALTER TABLE predictions ALTER COLUMN market DROP DEFAULT;` and the same for `outcomes`. **This file is not imported, referenced, or executed by any application code path or test — an operator must run it manually against the target database, once, deliberately**, per its own header comment. It has **not** been executed as part of this remediation pass. It does not rewrite any historical row — every existing row already has a concrete stored value; dropping the default only changes what a *future* `INSERT` that omits the column would receive.
- **`daily_picks_cache` is a separate table, outside this specific predictions/outcomes contamination boundary.** It stores the cached Daily Picks JSON payload (not a per-prediction row) and is written by `save_picks_to_db`/`load_picks_from_db`, which still carry their own `market: str = "IN"` default. This is deliberately left untouched: every current caller (`services/daily_picks.py`, `services/premarket_finalizer.py`) already passes `market` explicitly, so the default is dormant, not actively exercised; and this table isn't part of the `predictions`/`outcomes` contamination this phase addresses. Touching it risks unrelated behavior change to Daily Picks caching. This is a named, tracked residual — not claimed to be closed by this phase.
- **Read-only query defaults are a distinct, lower-risk category, not persistence defaults.** `get_training_data`, `get_unresolved_predictions`, `get_ambiguous_pending_predictions`, and `count_training_rows` (both backends) all still carry their own `market: str = "IN"` default. None of these functions writes to any table — a caller that omits `market` gets a query scoped to `IN` by default, which affects *what is read*, never *what is persisted*. This is intentionally out of scope for the persistence-boundary hardening described in this document.

## 10. Resolver conflict exclusion

`services/alpha_engine/outcome_logger.py`'s `resolve_pair()` fetches all eligible pending predictions, then partitions them into `pending` (usable) and `market_conflict_excluded` **before** `batch_limit` truncation — so a legacy contaminated row can no longer occupy a batch slot a genuinely resolvable row further down the list could have used. Emits `resolver_market_conflict_skipped` with `excluded_count`/`eligible_count` whenever at least one row is excluded. `UNKNOWN`-classified symbols are counted (`unknown_symbol_count`) but never excluded.

## 11. Manifest scanning and exhaustion semantics

`services/alpha_engine/manifest_backfill.py`'s `build_manifest()` scans the eligible pool in full deterministic order (`pred_date ASC, symbol ASC, prediction_id ASC`), excluding — not silently null-filling — three categories of row: definitive market/symbol conflicts, candidates whose forward-return values are all unresolvable, and candidates with zero fields left to populate (structurally rare given the eligibility query's own filter, but defended against).

There is **no `allow_partial` consent flag** (an earlier version of this phase had one; it was removed). Instead, every manifest is self-describing:

```
requested_candidate_count     — the batch_limit that was asked for
actual_candidate_count        — how many usable candidates were actually found
source_population_exhausted   — True iff the scan ran out of eligible rows
                                 before reaching batch_limit
```

`build_manifest` always returns a manifest whenever at least one usable candidate exists — it never refuses merely for "fewer than requested." `validate_manifest_structure` (the loader, called before any preflight/execution) enforces the invariants: `actual_candidate_count == len(candidates)`, `actual_candidate_count <= requested_candidate_count`, and — critically — an under-filled manifest (`actual < requested`) is rejected unless `source_population_exhausted` is explicitly `true`. A full manifest (`actual == requested`) carries no additional constraint on that flag's value.

**Manifest schema version `"2"` is a deliberate breaking safety boundary.** `MANIFEST_VERSION` was bumped `"1"` → `"2"` when the three fields above were introduced as mandatory, structurally-enforced fields. A forensic diff review of the first version-1-string implementation found that leaving `MANIFEST_VERSION` unchanged while adding real structural invariants meant an existing, valid, pre-existing Phase 1A.3 manifest (which claimed the same version string but lacked these fields) would be rejected only as an accidental side effect of a field-presence mismatch — not by a deliberate compatibility decision — with a confusing, misleading error message. This is now explicit:

- `manifest_version == "2"` (`CURRENT_MANIFEST_VERSIONS`) validates normally, with the invariants above enforced.
- `manifest_version == "1"` (`DEPRECATED_MANIFEST_VERSIONS`) is recognized and immediately rejected by `validate_manifest_structure` — before checksum verification, before any other check — with a purpose-built error explaining the manifest predates the current schema and must be regenerated from scratch. **A version-1 manifest is never automatically upgraded, rewritten, or executed** — `execute_manifest` calls `validate_manifest_structure` first, unconditionally, so a deprecated manifest never reaches `preflight_manifest` or any write.
- Any other `manifest_version` value fails closed with a distinct "unsupported manifest_version" error.

## 12. Zero / all-NULL / semantically-empty refusal rules

`build_manifest` raises `ManifestGenerationError` (and emits `manifest_generation_refused`) only when the usable-candidate count is exactly zero after all exclusions. A candidate whose every forward-return value is unresolvable, or whose `fields_to_populate` list is empty (nothing left to write), is excluded from the candidate list entirely before this check runs — so "all rows were unusable" and "zero usable rows found" collapse into the same, single fail-closed path.

## 13. Repair-plan deterministic JSON schema

**Not to be confused with §11's manifest schema version.** The repair plan's `manifest_version` field (confusingly similarly named — it's a legacy field name, retained rather than renamed to avoid an unrelated schema churn) is the repair-*planner's own* schema version, tracked independently via `PLAN_SCHEMA_VERSION`/`SUPPORTED_PLAN_SCHEMA_VERSIONS` in `scripts/plan_market_contamination_repair.py`. It has stayed `"1"` throughout this phase — only `services/alpha_engine/manifest_backfill.py`'s `MANIFEST_VERSION` (the outcome-backfill manifest's own, separate schema) was bumped to `"2"`.

`scripts/plan_market_contamination_repair.py` produces:

```
manifest_version          — plan file schema version ("1")
planner_version           — classification-logic version ("1")
source_commit             — git rev-parse HEAD at generation time
database_identity         — {host, port, dbname} only — never credentials
total_contaminated_rows   — len(candidates)
classification_counts     — {category: count} for all 5 categories
dependency_summary        — {dependency_domain: blocking_match_count}
canonical_serialization   — "sort_keys+compact_separators+no_nan"
residual_risk_notes       — verbatim list of known unresolved risks (§20)
candidates                — list of per-row classification results
manifest_sha256           — sha256 over the canonical JSON form, excluding itself
```

Per-candidate: `prediction_id`, `symbol`, `horizon`, `stored_market`, `pred_date`, `price`, `category`, `category_reason`, `matched_us_prediction_id`, `downstream_dependencies` (see §16).

**No wall-clock timestamp, random ID, or temporary/environment-dependent path is ever written into the file.** An earlier version included `generated_at_utc` inside the checksummed payload, making the checksum a function of generation time rather than pure data — this was found and removed; a human-readable timestamp is now printed to the console only, never persisted. Confirmed by direct test: identical synthetic input produces byte-identical `canonical_json` output and an identical `manifest_sha256` across repeated calls.

## 14. Checksum calculation and verification

Checksum: sha256 over `canonical_json()` (sorted keys, compact separators, `allow_nan=False`) of the plan dict with only the `manifest_sha256` key itself excluded — reused from `services/alpha_engine/manifest_backfill.compute_checksum`.

Verification: `python scripts/plan_market_contamination_repair.py --verify path/to/plan.json` calls `validate_repair_plan()`, which checks schema/planner version, every required top-level and per-candidate field, allowed classification categories, duplicate `prediction_id` detection, classification-count totals against the actual candidate list, finite numeric values, checksum recomputation/match, and the absence of any field name (`execute`, `apply`, `write`, `repair_action`, `confirm`, `mutate`) that would imply a mutation capability. This forbidden-field check is **recursive** (`_check_forbidden_fields_recursive`, bounded to `_MAX_VALIDATION_DEPTH` levels): it scans the plan's full structure, not just top-level and candidate-level keys — a forbidden field hidden inside a `downstream_dependencies` entry, or nested arbitrarily deep in any other structure, is caught with the exact path named in the error. (An earlier version of this check was shallow — top-level and per-candidate keys only — and a forensic diff review found a nested `downstream_dependencies` entry could carry a forbidden field undetected; fixed in the same pass that produced this document's current revision.) **`--verify` makes no database connection and reads no environment variable of any kind** — confirmed by the fact that `psycopg` is only imported inside `_connect_read_only()`, never at module scope, so `--verify` never even imports the database driver.

## 15. Repair classification precedence

Evaluated top to bottom, first match wins:

1. **`UNCLASSIFIABLE`** — insufficient/unclassifiable evidence: missing price/pred_date, or the symbol's current canonical classification is no longer definitively `US_ONLY`.
2. **`QUARANTINE_RECOMMENDED`** — a material downstream dependency exists (§16), checked **before** duplicate matching.
3. **`EXACT_CROSS_MARKET_DUPLICATE`** — a genuine `market='US'` row exists at the same `(symbol, pred_date, price)`, and no dependency blocked classification above.
4. **`NEAR_DUPLICATE_REVIEW_REQUIRED`** — a genuine `market='US'` row exists at the same `(symbol, pred_date)` but a different price, and no dependency blocked classification above.
5. **`SAFE_RELABEL_TO_US`** — no duplicate, no dependency: an orphaned mislabel.

This order was deliberately changed from an earlier version that let an exact/near-duplicate match win before a downstream-dependency check — that version could present a row with a real user-facing dependency (e.g. published as a Daily Pick) as an "exact duplicate" without ever surfacing the dependency. A contaminated row with a material dependency is never presented as automatically safe merely because a duplicate match also exists.

## 16. Downstream dependency confidence categories

Every candidate's `downstream_dependencies` is a list of four records (one per domain), each carrying `dependency_domain`, `match_type`, `match_key`, `match_count`, `confidence`, `blocks_automatic_relabel`, `reason_code`:

| Domain | Match type | Confidence | Rationale |
|---|---|---|---|
| `outcomes` | exact key (`symbol, horizon, market, pred_date`) | `DIRECT` | Tied to this exact row's own natural key |
| `daily_picks` | exact key (`prediction_id`) | `DIRECT` | The row's own `is_daily_pick` column |
| `paper_trades` | `symbol + market` | `STRONG_LOGICAL` | Real signal, but no FK back to a specific prediction id |
| `score_snapshots` | `symbol` only | `WEAK_LOGICAL` | The table has no `market` column at all — cannot distinguish IN-scoped from US-scoped |

All four are conservative: any `match_count > 0` sets `blocks_automatic_relabel = True`, even at `WEAK_LOGICAL` confidence — a false-positive `QUARANTINE_RECOMMENDED` is safe; a false-negative `SAFE_RELABEL_TO_US` is not. The confidence tier lets a human reviewer weigh a `DIRECT` block differently from a `WEAK_LOGICAL` one without the planner silently deciding that distinction itself.

## 17. India denominator exclusion behavior

Two production code paths read the `predictions` table for aggregate metrics — confirmed exhaustive by grep, no others exist:

- `get_daily_picks_performance` (`services/postgres_store.py`) — feeds the live `/picks/performance` endpoint. Had no market filter at all; now excludes definitive conflicts post-fetch.
- `get_training_data` (`services/postgres_store.py` and `services/alpha_engine/store.py`) — feeds the IC engine and meta-model. Excludes definitive conflicts that also happen to have a matching same-market outcome row.

Both: `BOTH` and `UNKNOWN` symbols remain included; valid `IN_ONLY`/`US_ONLY` rows remain included; numerator and denominator are both derived from the same single filtered list (the exclusion happens once, before any caller computes a ratio); each emits `performance_market_conflicts_excluded` with `fetched_count`/`excluded_count`/`eligible_count` whenever at least one row is excluded. Neither change touches ranking, scoring, or which rows are `is_daily_pick`.

**Filtering happens after fetching the complete bounded population, and there is no SQL `LIMIT` that excluded rows can consume.** Both queries fetch every row matching their `WHERE` bounds (horizon/window/market — confirmed by direct inspection of both SQL statements) with no `LIMIT` clause at all; exclusion happens entirely in Python afterward. This means a contaminated row can never "use up" a limited slot that a valid row would otherwise have occupied — the risk that shape of bug would create (a limit consumed by rows that get filtered out afterward, silently shrinking or biasing the effective sample) does not apply to either function.

## 18. Known limitations

- The database-level `DEFAULT 'IN'` on live production `predictions.market`/`outcomes.market` has **not** been dropped — see §9. Application-level enforcement is now complete for **both** `log_prediction` and `log_outcome`, in both backends; the schema-level migration is written but unexecuted, and dropping it does not itself close any known application-level gap (both write boundaries already always supply an explicit value at the SQL level, never relying on the column's own default either way).
- `score_snapshots` has no `market` column — its dependency signal is inherently symbol-only (§16); this is a pre-existing schema gap, not something this phase's scope permits fixing.
- `save_picks_to_db`/`load_picks_from_db` (`daily_picks_cache`) still carry a `market: str = "IN"` default — dormant (no current caller relies on it), out of scope, tracked in §9.
- The class-share alias fallback (§7) only handles the single documented `.`→`-` transform; it is not a general fuzzy-matching layer.

## 19. The unreconciled historical duplicate methodology

An earlier forensic pass (Phase 1A.5) reported 194 exact-price and 132 near-price duplicate matches using a methodology not fully retained in this remediation's available context. This planner's own duplicate-matching methodology (§15, items 3-4) produces its own counts when run against live data. **The two are not claimed to reconcile in this phase.** Reconciling them requires a separately authorized production analysis — this exact residual-risk note is embedded verbatim in every generated plan's `residual_risk_notes` field so it travels with the artifact, not just this document.

## 20. No backfill or repair has been executed

Nothing in this phase writes to `predictions`, `outcomes`, or any other production table. The repair planner has no `--execute`/`--apply`/`--write`/`--repair` mode of any kind (confirmed by both static source inspection and a dedicated regression test). No historical row has been relabeled, deleted, or quarantined as a result of this phase's work.

## 21. The Phase 1A.4 manifest is invalid

The manifest generated during the Phase 1A.4 investigation is a known-invalid artifact (it predates this phase's fail-closed exclusion logic and could be entirely composed of unresolvable contaminated rows) and **must never be reused** for any purpose, including as a template, a reference count, or an input to any future repair-planning work.

## 22. No production database or endpoint was accessed during this remediation

Both remediation passes described in this document (the initial `log_prediction`-only hardening, and the subsequent pass that hardened `log_outcome`, fixed the migration caller, bumped the manifest schema version, and added recursive repair-plan validation) used only source inspection, synthetic fixtures, temporary SQLite databases, and mocked database connections — no connection of any kind to any real database, local or remote, beyond the test suite's own ephemeral temp-file SQLite instances, and no call to any Railway or other production endpoint. No production credentials were read, and `~/.stocksense360_audit_env` was not sourced during either pass.
