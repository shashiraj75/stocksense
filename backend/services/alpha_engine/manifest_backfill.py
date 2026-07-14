"""
Manifest-locked outcome backfill (Phase 1A.3).

Why this exists: the operator backfill tool's candidate selection
(get_unresolved_predictions) previously had no ORDER BY and didn't even
select predictions.id, so a --batch-size slice could silently select a
different set of predictions from one run to the next — dry-run and a later
--execute were never provably the same batch. This module makes selection
deterministic, freezes a reviewed batch into a versioned, checksummed JSON
manifest (including the exact forward-return values computed at generation
time — execution never re-fetches prices, so what was reviewed is exactly
what gets written), re-validates every candidate against live state
immediately before any write (failing the whole batch closed on any drift,
never silently skipping/substituting), and applies the batch as one
transaction.

Three phases, each independently callable:
  build_manifest()    — deterministic selection + price preview, zero writes
  preflight_manifest() — live re-validation against a saved manifest, zero writes
  execute_manifest()   — preflight + one all-or-nothing transactional write
"""

import json
import hashlib
import subprocess
from datetime import datetime, timezone

from services.alpha_engine import outcome_logger
from services.alpha_engine import store


# Phase 1A.6 remediation: bumped 1 -> 2. Version 1 lacked
# requested_candidate_count / actual_candidate_count / source_population_
# exhausted — mandatory fields this remediation added, with real structural
# invariants (see validate_manifest_structure). Silently accepting a version-1
# manifest under an unchanged version string would have let those invariants
# go unenforced on old files by accident (a missing-field side effect, not a
# deliberate compatibility decision) — see the forensic diff review that
# flagged this as a blocker. Version 1 is now explicitly deprecated: it is
# recognized (so it gets a clear, purpose-built compatibility error) but
# never validated, preflighted, or executed. It is never auto-upgraded or
# rewritten — a deprecated manifest must be regenerated from scratch.
MANIFEST_VERSION = "2"
CURRENT_MANIFEST_VERSIONS = {"2"}
DEPRECATED_MANIFEST_VERSIONS = {"1"}

# Sanity ceiling on a single manifest's size — well above a canary (200) or a
# moderate staged batch, far below the full multi-thousand-row backlog for a
# market/horizon. Prevents an accidentally-unbounded manifest (e.g. a bad
# --batch-size) from holding row locks across an unreasonably large batch for
# an extended transaction.
MAX_MANIFEST_CANDIDATES = 2000


class ManifestValidationError(Exception):
    """Structural problem with a manifest itself (version, checksum, duplicate
    IDs, count mismatch) — raised before any live-state check is attempted."""


class ManifestPreflightError(Exception):
    """One or more manifest candidates have drifted from live state since the
    manifest was generated. Carries the full list of issues found (never just
    the first) so the whole batch can be reported and aborted before any write."""

    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__(f"{len(issues)} manifest candidate(s) failed preflight: "
                          f"{[i['prediction_id'] for i in issues]}")


def _get_source_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
            cwd=__file__.rsplit("/services/", 1)[0],
        ).stdout.strip() or None
    except Exception:
        return None


def canonical_json(obj) -> str:
    """Deterministic serialization: sorted keys, compact separators, stable
    string form for any date objects — the same logical manifest always
    produces the same bytes, regardless of dict insertion order or platform.
    allow_nan=False rejects NaN/Infinity/-Infinity outright (Python's json
    module otherwise silently emits the non-spec-compliant literals NaN/
    Infinity) — a manifest must never freeze a malformed numeric value, and
    NaN would also silently break drift comparison (NaN != NaN is always
    true in IEEE754, which would make every re-check of such a value look
    like spurious drift)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)


def compute_checksum(manifest_without_checksum: dict) -> str:
    """sha256 over the canonical JSON form of the manifest with the
    manifest_sha256 field itself excluded (it can't include its own hash)."""
    payload = {k: v for k, v in manifest_without_checksum.items() if k != "manifest_sha256"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _horizon_config_for(horizon: str) -> tuple:
    for cfg in outcome_logger.HORIZON_CONFIG:
        if cfg[0] == horizon:
            return cfg
    raise ValueError(f"unknown horizon: {horizon!r}")


class ManifestGenerationError(Exception):
    """Raised when build_manifest cannot produce a usable manifest at all —
    zero usable candidates found anywhere in the eligible pool. There is no
    separate "insufficient count" refusal: a manifest with 1 <= N <
    batch_limit usable candidates is always returned, explicitly marked
    self-describing (requested_candidate_count, actual_candidate_count,
    source_population_exhausted) rather than requiring a caller-supplied
    consent flag — see build_manifest's docstring."""


def build_manifest(market: str, horizon: str, *, start_date: str | None = None,
                    end_date: str | None = None, batch_limit: int = 500) -> dict:
    """
    Deterministically select up to batch_limit oldest-first, unambiguous,
    incomplete, USABLE candidates for (market, horizon) — "usable" meaning:
    no definitive market/symbol conflict, at least one forward-return value
    actually resolves, and at least one outcome column remains meaningfully
    unpopulated. Preview-resolves each candidate's real forward-return
    values now (read-only price lookups, no DB writes), and freezes the
    result into a versioned, checksummed manifest dict.

    Phase 1A.6: candidates are scanned in full deterministic order
    (pred_date ASC, symbol ASC, prediction_id ASC) and definitively
    mislabeled, unresolvable, or semantically-empty rows are EXCLUDED and
    skipped over — not silently included with null values — so a manifest
    keeps scanning until it either collects batch_limit usable candidates
    or exhausts the eligible pool. This directly fixes the Phase 1A.4
    finding: a manifest can no longer end up entirely composed of legacy
    contaminated rows that can never resolve.

    There is no allow_partial consent flag. Instead, the manifest always
    tells the truth about how complete it is:
      requested_candidate_count — the batch_limit that was asked for
      actual_candidate_count    — how many usable candidates were found
      source_population_exhausted — True iff the scan ran out of eligible
                                     rows before reaching batch_limit
    An under-filled manifest (actual < requested) is only ever produced
    with source_population_exhausted=True — the loader/executor
    (validate_manifest_structure) enforces this invariant on every load,
    so a hand-edited or otherwise-inconsistent manifest is rejected before
    any live-state check.

    Fails closed (raises ManifestGenerationError) only when zero usable
    candidates exist anywhere in the eligible pool — never for merely
    "fewer than requested."
    """
    if market not in outcome_logger.MARKETS:
        raise ValueError(f"unknown market: {market!r}")
    horizon_name, min_days, d1, d5, d20, d60 = _horizon_config_for(horizon)

    from services.market_integrity import (
        EVENT_MANIFEST_CONFLICT_SKIPPED, EVENT_MANIFEST_GENERATION_REFUSED,
        EVENT_MANIFEST_POPULATION_EXHAUSTED, REASON_DEFINITIVE_CONFLICT,
        REASON_POPULATION_EXHAUSTED_UNDERFILLED, REASON_ZERO_USABLE_CANDIDATES,
        SymbolMarketClass, classify_symbol, emit_market_event, is_definitive_conflict,
    )
    import logging

    all_eligible = store.get_unresolved_predictions(horizon_name, min_days_old=min_days, market=market)
    if start_date or end_date:
        all_eligible = [c for c in all_eligible
                        if outcome_logger._in_date_range(c["pred_date"], start_date, end_date)]
    eligible_scanned = len(all_eligible)

    candidates = []
    market_conflict_excluded = []
    unknown_symbol_count = 0
    missing_price_excluded = []
    semantically_empty_excluded = []
    source_population_exhausted = True

    for c in all_eligible:
        if len(candidates) >= batch_limit:
            source_population_exhausted = False
            break

        if is_definitive_conflict(c["symbol"], market):
            market_conflict_excluded.append({"prediction_id": c["prediction_id"], "symbol": c["symbol"]})
            continue
        if classify_symbol(c["symbol"]) == SymbolMarketClass.UNKNOWN:
            unknown_symbol_count += 1

        pred_date_str = str(c["pred_date"])
        existing = store.get_outcome_snapshot(c["symbol"], horizon_name, market, pred_date_str)

        r1 = outcome_logger._fetch_return(c["symbol"], pred_date_str, 1, market) if d1 else None
        r5 = outcome_logger._fetch_return(c["symbol"], pred_date_str, 5, market) if d5 else None
        r20 = outcome_logger._fetch_return(c["symbol"], pred_date_str, 20, market) if d20 else None
        r60 = outcome_logger._fetch_return(c["symbol"], pred_date_str, 60, market) if d60 else None

        if all(v is None for v in (r1, r5, r20, r60)):
            missing_price_excluded.append({"prediction_id": c["prediction_id"], "symbol": c["symbol"]})
            continue

        existing_fields = {"return_1d": None, "return_5d": None, "return_20d": None, "return_60d": None}
        existing_id = None
        if existing is not None:
            existing_id = existing["id"]
            existing_fields = {k: existing[k] for k in existing_fields}

        fields_to_populate = [
            col for col, val in (("return_1d", existing_fields["return_1d"]),
                                  ("return_5d", existing_fields["return_5d"]),
                                  ("return_20d", existing_fields["return_20d"]),
                                  ("return_60d", existing_fields["return_60d"]))
            if val is None and {"return_1d": d1, "return_5d": d5,
                                 "return_20d": d20, "return_60d": d60}[col]
        ]

        if not fields_to_populate:
            # Structurally shouldn't occur — get_unresolved_predictions'
            # own eligibility clause already requires at least one
            # horizon-relevant column to still be NULL — but excluded
            # defensively: a candidate with nothing left to write
            # represents no meaningful permitted action and must never
            # occupy a manifest slot.
            semantically_empty_excluded.append({"prediction_id": c["prediction_id"], "symbol": c["symbol"]})
            continue

        candidates.append({
            "prediction_id": c["prediction_id"],
            "symbol": c["symbol"],
            "market": market,
            "prediction_date": pred_date_str,
            "prediction_horizon": horizon_name,
            "reference_price": c["price"],
            "existing_outcome_id": existing_id,
            "existing_outcome_fields": existing_fields,
            "fields_to_populate": fields_to_populate,
            "resolved_values": {
                "return_1d": r1, "return_5d": r5, "return_20d": r20, "return_60d": r60,
            },
            "expected_operation": "INSERT" if existing_id is None else "UPDATE",
        })

    if market_conflict_excluded:
        emit_market_event(
            EVENT_MANIFEST_CONFLICT_SKIPPED,
            f"[manifest_backfill] [{market}/{horizon_name}] skipped {len(market_conflict_excluded)} "
            f"definitive market/symbol conflict(s) before usable-candidate selection",
            level=logging.WARNING, reason_code=REASON_DEFINITIVE_CONFLICT, market=market,
            excluded_count=len(market_conflict_excluded), eligible_count=eligible_scanned,
        )

    ambiguous_excluded = store.get_ambiguous_pending_predictions(horizon_name, min_days_old=min_days, market=market)

    requested_candidate_count = batch_limit
    actual_candidate_count = len(candidates)

    scan_report = {
        "eligible_scanned": eligible_scanned,
        "usable_candidates": actual_candidate_count,
        "market_conflict_exclusions": len(market_conflict_excluded),
        "unknown_symbol_warnings": unknown_symbol_count,
        "ambiguous_group_exclusions": len(ambiguous_excluded),
        "missing_price_exclusions": len(missing_price_excluded),
        "semantically_empty_exclusions": len(semantically_empty_excluded),
        "requested_batch_limit": batch_limit,
        "pool_exhausted_before_limit": source_population_exhausted,
    }

    if actual_candidate_count == 0:
        emit_market_event(
            EVENT_MANIFEST_GENERATION_REFUSED,
            f"[manifest_backfill] REFUSED — zero usable candidates for market={market!r} "
            f"horizon={horizon_name!r} (scanned {eligible_scanned} eligible rows)",
            level=logging.ERROR, reason_code=REASON_ZERO_USABLE_CANDIDATES, market=market,
            eligible_count=eligible_scanned,
        )
        raise ManifestGenerationError(
            f"zero usable candidates for market={market!r} horizon={horizon_name!r} "
            f"(scanned {eligible_scanned} eligible rows; "
            f"{len(market_conflict_excluded)} market-conflict, "
            f"{len(missing_price_excluded)} missing-price, "
            f"{len(semantically_empty_excluded)} semantically-empty exclusions). "
            f"Refusing to produce an empty manifest."
        )

    if source_population_exhausted and actual_candidate_count < requested_candidate_count:
        emit_market_event(
            EVENT_MANIFEST_POPULATION_EXHAUSTED,
            f"[manifest_backfill] [{market}/{horizon_name}] eligible pool exhausted at "
            f"{actual_candidate_count} of {requested_candidate_count} requested usable candidates",
            level=logging.WARNING, reason_code=REASON_POPULATION_EXHAUSTED_UNDERFILLED, market=market,
            requested_count=requested_candidate_count, actual_count=actual_candidate_count,
        )

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": market,
        "horizon": horizon_name,
        "start_date": start_date,
        "end_date": end_date,
        "requested_candidate_count": requested_candidate_count,
        "actual_candidate_count": actual_candidate_count,
        "candidate_count": actual_candidate_count,
        "source_population_exhausted": source_population_exhausted,
        "ordering": "pred_date ASC, symbol ASC, prediction_id ASC",
        "source_commit": _get_source_commit(),
        "scan_report": scan_report,
        "candidates": candidates,
    }
    manifest["manifest_sha256"] = compute_checksum(manifest)
    return manifest


def validate_manifest_structure(manifest: dict, expected_sha256: str | None = None) -> None:
    """
    Structural checks only — no DB access. Raises ManifestValidationError on
    the first problem found (these are all-or-nothing prerequisites, not
    per-candidate drift).
    """
    version = manifest.get("manifest_version")
    if version in DEPRECATED_MANIFEST_VERSIONS:
        raise ManifestValidationError(
            f"manifest_version={version!r} predates the current schema "
            f"(current: {MANIFEST_VERSION!r}) and cannot be validated, preflighted, "
            f"or executed — mandatory fields (requested_candidate_count, "
            f"actual_candidate_count, source_population_exhausted) were added in the "
            f"current schema and are not present in this format. This manifest must be "
            f"regenerated from scratch under the current schema; it is never "
            f"automatically upgraded or rewritten."
        )
    if version not in CURRENT_MANIFEST_VERSIONS:
        raise ManifestValidationError(f"unsupported manifest_version: {version!r}")

    recorded_sha = manifest.get("manifest_sha256")
    recomputed_sha = compute_checksum(manifest)
    if recorded_sha != recomputed_sha:
        raise ManifestValidationError(
            f"manifest checksum mismatch: file claims {recorded_sha!r} but content "
            f"hashes to {recomputed_sha!r} — the manifest may have been tampered with"
        )
    if expected_sha256 is not None and expected_sha256 != recorded_sha:
        raise ManifestValidationError(
            f"--manifest-sha256 {expected_sha256!r} does not match this manifest's "
            f"own recorded checksum {recorded_sha!r}"
        )

    candidates = manifest.get("candidates", [])
    if len(candidates) > MAX_MANIFEST_CANDIDATES:
        raise ManifestValidationError(
            f"manifest has {len(candidates)} candidates, exceeding the "
            f"MAX_MANIFEST_CANDIDATES safety bound of {MAX_MANIFEST_CANDIDATES} — "
            f"generate and execute smaller, staged batches instead"
        )
    if len(candidates) != manifest.get("candidate_count"):
        raise ManifestValidationError(
            f"candidate_count={manifest.get('candidate_count')} does not match "
            f"actual candidates array length {len(candidates)}"
        )

    requested = manifest.get("requested_candidate_count")
    actual = manifest.get("actual_candidate_count")
    exhausted = manifest.get("source_population_exhausted")
    if actual != len(candidates):
        raise ManifestValidationError(
            f"actual_candidate_count={actual} does not match actual candidates array "
            f"length {len(candidates)}"
        )
    if requested is not None and actual is not None and actual > requested:
        raise ManifestValidationError(
            f"actual_candidate_count={actual} exceeds requested_candidate_count={requested} "
            f"— a manifest can never contain more candidates than were requested"
        )
    if requested is not None and actual is not None and actual < requested and not exhausted:
        raise ManifestValidationError(
            f"manifest is under-filled (actual_candidate_count={actual} < "
            f"requested_candidate_count={requested}) but source_population_exhausted is "
            f"not true — an under-filled manifest must always be explicitly marked exhausted"
        )

    from services.market_integrity import is_definitive_conflict

    seen_ids = set()
    for c in candidates:
        pid = c.get("prediction_id")
        if pid in seen_ids:
            raise ManifestValidationError(f"duplicate prediction_id {pid} in manifest")
        seen_ids.add(pid)
        if c.get("market") != manifest.get("market"):
            raise ManifestValidationError(
                f"candidate {pid} market {c.get('market')!r} does not match "
                f"manifest market {manifest.get('market')!r}"
            )
        # Defense in depth: build_manifest never includes a definitive
        # conflict, but a hand-edited or otherwise-tampered manifest file
        # must still be caught here before any live-state check runs.
        if is_definitive_conflict(c.get("symbol", ""), c.get("market", "")):
            raise ManifestValidationError(
                f"candidate {pid} ({c.get('symbol')!r}) has a definitive market/symbol "
                f"conflict under market={c.get('market')!r} — a manifest must never "
                f"include one, even if hand-edited"
            )


def preflight_manifest(manifest: dict) -> list[dict]:
    """
    Live re-validation of every manifest candidate against current DB state.
    Returns a list of ALL issues found (never just the first) — empty list
    means every candidate is still exactly as the manifest recorded it.
    Read-only: makes no writes.
    """
    market = manifest["market"]
    horizon = manifest["horizon"]
    _, min_days, *_ = _horizon_config_for(horizon)
    candidates = manifest["candidates"]

    ids = [c["prediction_id"] for c in candidates]
    snapshot = store.get_predictions_snapshot(ids)

    today = datetime.now(timezone.utc).date()
    issues = []

    for c in candidates:
        pid = c["prediction_id"]
        live = snapshot.get(pid)

        if live is None:
            issues.append({"prediction_id": pid, "reason": "prediction no longer exists"})
            continue
        if live["market"] != c["market"]:
            issues.append({"prediction_id": pid,
                            "reason": f"market drifted: manifest={c['market']!r} live={live['market']!r}"})
            continue
        if live["horizon"] != c["prediction_horizon"]:
            issues.append({"prediction_id": pid,
                            "reason": f"horizon drifted: manifest={c['prediction_horizon']!r} "
                                      f"live={live['horizon']!r}"})
            continue
        if live["symbol"] != c["symbol"]:
            issues.append({"prediction_id": pid,
                            "reason": f"symbol drifted: manifest={c['symbol']!r} live={live['symbol']!r}"})
            continue
        if str(live["pred_date"]) != c["prediction_date"]:
            issues.append({"prediction_id": pid,
                            "reason": f"prediction_date drifted: manifest={c['prediction_date']!r} "
                                      f"live={live['pred_date']!r}"})
            continue
        if live["price"] != c["reference_price"]:
            issues.append({"prediction_id": pid,
                            "reason": f"reference_price drifted: manifest={c['reference_price']!r} "
                                      f"live={live['price']!r}"})
            continue

        age_days = (today - outcome_logger._to_date(live["pred_date"])).days
        if age_days < min_days:
            issues.append({"prediction_id": pid,
                            "reason": f"no longer old enough for horizon {horizon} "
                                      f"(age={age_days}d, needs >={min_days}d)"})
            continue

        current_outcome = store.get_outcome_snapshot(c["symbol"], horizon, market, c["prediction_date"])
        expected_id = c["existing_outcome_id"]
        if current_outcome is None:
            if expected_id is not None:
                issues.append({"prediction_id": pid,
                                "reason": f"manifest expected existing outcome id={expected_id}, "
                                          f"now missing"})
                continue
        else:
            if expected_id is None:
                issues.append({"prediction_id": pid,
                                "reason": f"outcome row id={current_outcome['id']} now exists but "
                                          f"manifest recorded none — likely resolved by the normal "
                                          f"resolver since manifest generation"})
                continue
            for col in ("return_1d", "return_5d", "return_20d", "return_60d"):
                if c["existing_outcome_fields"].get(col) is not None and \
                   current_outcome[col] != c["existing_outcome_fields"][col]:
                    issues.append({"prediction_id": pid,
                                    "reason": f"existing outcome field {col} drifted: "
                                              f"manifest={c['existing_outcome_fields'][col]!r} "
                                              f"live={current_outcome[col]!r}"})
                    break
            else:
                for col in c["fields_to_populate"]:
                    if current_outcome[col] is not None:
                        issues.append({"prediction_id": pid,
                                        "reason": f"intended field {col} is no longer NULL "
                                                  f"(now {current_outcome[col]!r}) — likely resolved "
                                                  f"by the normal resolver since manifest generation"})
                        break

        if store.check_ambiguous_for_key(c["symbol"], horizon, market, c["prediction_date"]):
            issues.append({"prediction_id": pid,
                            "reason": "relationship has become ambiguous (a new conflicting-price "
                                      "prediction was logged since manifest generation)"})

    return issues


def execute_manifest(manifest: dict, expected_sha256: str, *, dry_run: bool = True) -> dict:
    """
    Full pipeline: structural validation -> preflight -> (if not dry_run)
    one transactional batch write. Raises ManifestValidationError or
    ManifestPreflightError before any write if anything is wrong; never
    partially applies a batch.

    source_commit mismatch is reported, not fatal: the manifest's own
    candidate data is independently re-validated by preflight regardless of
    which commit is currently checked out, so a commit difference alone
    (e.g. an unrelated doc/test change landed since generation) shouldn't
    block an otherwise-clean batch. It's surfaced in the result so an
    operator can judge whether the difference is meaningful. Note: a
    manifest generated against an uncommitted working tree records the
    *last commit*, not the uncommitted changes — source_commit is only a
    meaningful signal for manifests generated after the intended code has
    actually been committed and deployed.
    """
    validate_manifest_structure(manifest, expected_sha256)
    issues = preflight_manifest(manifest)
    if issues:
        raise ManifestPreflightError(issues)

    current_commit = _get_source_commit()
    source_commit_mismatch = (
        manifest.get("source_commit") is not None
        and current_commit is not None
        and manifest["source_commit"] != current_commit
    )

    if dry_run:
        return {"dry_run": True, "would_write": len(manifest["candidates"]), "issues": [],
                "source_commit_mismatch": source_commit_mismatch,
                "manifest_source_commit": manifest.get("source_commit"),
                "current_source_commit": current_commit}

    writes = []
    for c in manifest["candidates"]:
        writes.append({
            "symbol": c["symbol"], "horizon": c["prediction_horizon"], "market": c["market"],
            "pred_date": c["prediction_date"],
            "return_1d": c["resolved_values"]["return_1d"],
            "return_5d": c["resolved_values"]["return_5d"],
            "return_20d": c["resolved_values"]["return_20d"],
            "return_60d": c["resolved_values"]["return_60d"],
            "expected_existing": {"id": c["existing_outcome_id"], **c["existing_outcome_fields"]},
        })

    written = store.execute_outcome_writes_transactional(writes)
    return {"dry_run": False, "written": written, "issues": [],
            "source_commit_mismatch": source_commit_mismatch,
            "manifest_source_commit": manifest.get("source_commit"),
            "current_source_commit": current_commit}
