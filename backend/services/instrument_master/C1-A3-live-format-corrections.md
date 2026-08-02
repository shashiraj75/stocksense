# Phase C1-A3 — NSE source-contract corrections from C1-A2 live reconnaissance

This note documents the source-contract corrections made to
`source_registry.py` / `source_validators.py` after the Phase C1-A2
live reconnaissance fetched and inspected all 11 approved NSE
`nsearchives.nseindia.com` source URLs from a main-site-reachable
execution environment. It supplements, and does not replace, the
module-level docstrings in those two files, which remain the primary
reference for each contract's exact rules.

**Scope note:** these corrections align the source registry's
*declared contracts* with what C1-A2 actually observed in live
response bodies. They are not a production-ingestion approval — see
"What this does NOT mean" below.

## Findings and corrections

### SME_EQUITY_L.csv (`nse_sme_current`)

- **Reachability**: previously `NOT_VERIFIED` (only ever confirmed via
  a human browser, never a tested automated execution environment).
  C1-A2 retrieved this exact URL with a plain, unauthenticated HTTPS
  GET — no cookies, no session priming, no special headers — from this
  repository's own execution environment. That is now recorded as
  `current_environment_reachability_status = VERIFIED`. Railway and
  GitHub Actions specifically remain untested; see "Remaining
  unresolved item" below.
- **Format correction**: every real row, and the header row itself,
  carries a trailing comma after `FACE_VALUE`, producing an 8th,
  always-empty raw CSV field beyond the 7 declared semantic columns.
  `validate_sme()` now tolerates exactly one optional trailing field,
  accepted only when empty/whitespace (silently dropped during
  normalization, never exposed as a business value), and rejects a row
  outright if that field is non-empty or if more than 8 raw fields are
  present. `maximum_field_count` in the registry entry is updated to 8
  to reflect the observed raw width; `expected_headers` (7 columns)
  is unchanged.

### symbolchange.csv (`nse_symbol_history`)

- **Field-order correction**: the live response body's real column
  order is `(company_or_scheme_name, old_symbol, new_symbol,
  effective_date)`. The registry's `secondary_identity_fields` had
  previously declared `(old_symbol, new_symbol, company_name, date)` —
  wrong order, though the validator's own date check (indexing
  `row[3]`) happened to still be correct by coincidence.
- **Fix**: `source_validators.py` now defines a single named-field type,
  `SymbolChangeRecord`, and a `parse_symbol_change_row()` function that
  is the one place in this module allowed to map a raw symbolchange.csv
  row to named fields. `validate_symbol_history()` uses it internally
  for its date check, so the old, incorrect ordering cannot silently
  reappear through a bare positional lookup elsewhere. The registry's
  `secondary_identity_fields` now matches this same order exactly.

### PREF.csv (`nse_preference`)

- **Grain resolved**: live evidence (symbol `JSWSTEEL`, ISIN
  `INE019A04016`) showed 4 rows sharing the identical trailing ISIN,
  differing only by `REDEMPTION DATE` — one row per redemption tranche
  of the same preference-share issuance. Grain is recorded as
  `PER_REDEMPTION_TRANCHE` on the registry entry.
- **Fix**: `validate_preference()` is now its own dedicated validator
  (previously shared `_validate_trailing_isin_source()` with
  `validate_warrant()`), keyed on full-row identity — every one of the
  14 declared fields plus the trailing ISIN, stripped — rather than the
  trailing ISIN alone. Two rows are only ever treated as a duplicate
  when every field agrees; distinct tranches sharing an ISIN are never
  collapsed, only a genuine exact-duplicate row is.
  (Independent-review correction: an earlier version of this fix keyed
  identity on the narrower composite `(ISIN, REDEMPTION DATE,
  CONVERSION DATE)`. That still lost data — two tranches sharing an
  ISIN and both dates, but differing only in `REDEMPTION AMT` or
  `CONVERSION AMT`, would have been wrongly collapsed. Full-row identity
  closes that gap and is provably conservative: it can only ever be
  *less* aggressive at merging rows than any field-subset composite.)
  `validate_warrant()` is unaffected and still uses the ISIN-only
  shared helper — no live evidence of a repeated-ISIN pattern was found
  for WARRANT.csv (only one real row was available to inspect).

### WARRANT.csv (`nse_warrant`)

- No format defect found beyond what was already declared (trailing
  undeclared ISIN field). Confirmed live: 1 real row (`ELECTCAST`), 6
  comma-fields. `validate_warrant()`'s existing strict-ISIN check on
  the trailing field is unchanged; docstring and registry
  `primary_identity_field` note clarified to state explicitly that the
  trailing field is validated as an ISIN, never accepted as an
  arbitrary extra column.

### REITS_L.csv / INVITS_L.csv (`nse_reit_current` / `nse_invit_current`)

- Confirmed live: exactly 3 real rows + 1 blank row + 1
  `"Note: the Market lot is updated..."` footnote row for each file.
  Matches the existing `footnote_filter` handling in
  `_validate_dedicated_current_source()` exactly — no code change
  required, only registry documentation updated with the live-observed
  row counts.

### IDR_W9.csv (`nse_idr`)

- Confirmed live: 1 real row (`STAN` / Standard Chartered PLC) + 2
  blank rows + 1 `"*"`-prefixed footnote row. Matches the existing
  footnote/blank-row handling exactly — no code change required.

### eq_ilseclist.csv (`nse_il_series`)

- Confirmed live: header-only response body (zero illiquid securities
  currently listed), matching `IL_SERIES_VALID_EMPTY_RAW_BYTES` exactly.
  `validate_il_series()`'s existing exact-byte `VALID_EMPTY` handling is
  unchanged and confirmed correct — a header-only file parses
  successfully, produces zero records, and is never classified as a
  source failure.

### namechange.csv (`nse_name_history`)

- Confirmed live: exact-duplicate rows genuinely occur (e.g. `BCG`,
  `CHENNPETRO`, `DHANILOANS`, `DHANIPP`, `INCRED` all appeared as
  byte-identical duplicate lines). `validate_name_history()`'s existing
  deterministic exact-duplicate collapsing (order-independent) is
  unchanged and confirmed correct.
- **New**: `dedupe_name_history_rows()` is added as its own public
  function so a downstream caller (or a test) can observe the actual
  deduplicated, first-occurrence-wins, stable-input-order row sequence
  directly, not merely a count. Near-duplicates (any row differing in
  even one field) are never collapsed by this function.

## Source-quality metadata: three-way reachability split

Every `SourceRegistryEntry` previously carried one
`automated_reachability_status` field that conflated "reachable from
whatever environment last checked it" with "approved for production
automation." That ambiguity is now split into three fields:

| Field | Meaning |
|---|---|
| `current_environment_reachability_status` | This repository's own execution environment, as directly evidenced by C1-A2 (all 11 sources: `VERIFIED`) |
| `production_environment_reachability_status` | Railway and GitHub Actions specifically — untested by C1-A2, remains `NOT_VERIFIED` for all 11 sources |
| `automated_reachability_status` (legacy) | Kept for backward compatibility, pinned exactly to `production_environment_reachability_status` (enforced in `__post_init__`) — the more conservative reading, so an existing caller reading only this field is never misled into believing production approval |

`content_contract_status` tracks a separate question — whether a
source's declared header/field-count contract has been checked against
a real response body (`CONTENT_VERIFIED_LIVE`, true for all 11 as of
C1-A2) versus only ever documented from static/secondary evidence
(`SCHEMA_ONLY_UNVERIFIED`).

## What this does NOT mean

- **Not a production-ingestion approval.** None of these corrections
  change `criticality`, `publication_blocking_status`, or any consumer
  of `manifest_contract.py` — approval for Daily Picks or any other
  production consumer remains a separate, not-yet-made decision.
- **Not evidence about Railway/GitHub Actions.** Reachability confirmed
  from one environment is never treated as evidence for another —
  `production_environment_reachability_status` stays `NOT_VERIFIED`
  for every source regardless of the current-environment result.

## Remaining unresolved item

Production-automation reachability (Railway and GitHub Actions
specifically) is untested by this reconnaissance and remains the sole
open item — see `production_environment_reachability_status` on every
registry entry.
