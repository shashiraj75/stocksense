"""
Trade Postmortem Sprint 3A, Stage J4C — level-history and price-basis
eligibility.

Pure, deterministic, no I/O. Separates three questions the J4B
observed-crossing layer never answers on its own:

  1. Is this specific level's post-entry modification history reliably
     known (level-history STATUS)?
  2. What do the entry/exit snapshot values for that level actually
     show (ENDPOINT evidence)?
  3. Is the acquired price evidence on a basis compatible with the
     trade's unadjusted stop/target values (PRICE-BASIS eligibility)?

None of these three functions consumes or produces a governed touch or
order CONCLUSION — that mapping lives in
governed_price_path_conclusions.py. A numerical crossing (J4B) is an
observation; it is never, by itself, proof that a configured and
active stop or target was hit.
"""
import math
from dataclasses import dataclass

# --- Level-history status (Stage J4C, Gate 1C) ---
LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY = "UNKNOWN_OR_LEGACY"
LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY = "MODIFIED_AFTER_ENTRY"
LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED = "GOVERNED_UNMODIFIED"
LEVEL_HISTORY_STATUS_CONTRADICTORY = "CONTRADICTORY"
LEVEL_HISTORY_STATUS_REQUIRED_EVIDENCE_MISSING = "REQUIRED_EVIDENCE_MISSING"

ALL_LEVEL_HISTORY_STATUSES = frozenset({
    LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY, LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY,
    LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED, LEVEL_HISTORY_STATUS_CONTRADICTORY,
    LEVEL_HISTORY_STATUS_REQUIRED_EVIDENCE_MISSING,
})

# The durable J4D invariant-version identity this semantic layer
# recognizes as governed. Any other non-null version fails closed to
# CONTRADICTORY rather than being silently trusted.
LEVEL_HISTORY_CONTRACT_VERSION_1 = "1"
SUPPORTED_LEVEL_HISTORY_CONTRACT_VERSIONS = frozenset({LEVEL_HISTORY_CONTRACT_VERSION_1})


def classify_level_history_status(*, invariant_version, level_modified_flag) -> str:
    """`invariant_version` is the durable per-trade
    level_history_contract_version (None for every historical/legacy
    trade). `level_modified_flag` is the per-level tri-state history
    flag (stop_modified_after_entry or target_modified_after_entry) —
    None means unknown/legacy, True/False are the only other permitted
    values. A version string outside SUPPORTED_LEVEL_HISTORY_CONTRACT_
    VERSIONS always fails closed to CONTRADICTORY, regardless of flag
    value, since Wave A can never trust a contract shape it doesn't
    recognize."""
    # Wave A closure correction — exact built-in type only (type() is,
    # not isinstance) so a hostile str/bool subclass fails closed rather
    # than being silently accepted because it happens to compare equal.
    if invariant_version is not None and type(invariant_version) is not str:
        return LEVEL_HISTORY_STATUS_CONTRADICTORY
    if invariant_version is not None and not invariant_version.strip():
        return LEVEL_HISTORY_STATUS_CONTRADICTORY
    if level_modified_flag is not None and type(level_modified_flag) is not bool:
        return LEVEL_HISTORY_STATUS_CONTRADICTORY

    governed_version = invariant_version in SUPPORTED_LEVEL_HISTORY_CONTRACT_VERSIONS

    if invariant_version is not None and not governed_version:
        return LEVEL_HISTORY_STATUS_CONTRADICTORY

    if level_modified_flag is True:
        # Positive TRUE evidence is trusted even on a legacy (version is
        # None) row — a directly observed future edit is reliable
        # regardless of whether the rest of that trade's history is
        # governed (Gate 1D "legacy positive-evidence" behavior).
        return LEVEL_HISTORY_STATUS_MODIFIED_AFTER_ENTRY

    if level_modified_flag is False:
        if not governed_version:
            # An untrusted legacy FALSE must never be treated as
            # governed-unmodified (Gate 1C.C) — only a supported
            # invariant version makes FALSE reliable.
            return LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY
        return LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED

    # level_modified_flag is None.
    if governed_version:
        # A governed-version trade with a NULL per-level flag is an
        # impossible state under the J4D invariant (new governed trades
        # always initialize FALSE) — fail closed rather than guess.
        return LEVEL_HISTORY_STATUS_CONTRADICTORY
    return LEVEL_HISTORY_STATUS_UNKNOWN_OR_LEGACY


# --- Endpoint evidence (Stage J4C, Gate 1E) ---
ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING = "ENTRY_SNAPSHOT_MISSING"
ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING = "EXIT_SNAPSHOT_MISSING"
ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING = "BOTH_SNAPSHOTS_MISSING"
ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS = "NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS"
ENDPOINT_EVIDENCE_NULL_TO_VALUE = "NULL_TO_VALUE"
ENDPOINT_EVIDENCE_VALUE_TO_NULL = "VALUE_TO_NULL"
ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES = "IDENTICAL_NONNULL_VALUES"
ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES = "DIFFERENT_NONNULL_VALUES"
ENDPOINT_EVIDENCE_MALFORMED_VALUE = "MALFORMED_VALUE"

ALL_ENDPOINT_EVIDENCE_STATUSES = frozenset({
    ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING, ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING,
    ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING, ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS,
    ENDPOINT_EVIDENCE_NULL_TO_VALUE, ENDPOINT_EVIDENCE_VALUE_TO_NULL,
    ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES, ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES,
    ENDPOINT_EVIDENCE_MALFORMED_VALUE,
})


def _is_well_formed_endpoint_value(value) -> bool:
    # Wave A closure correction — exact built-in int/float only; a bool
    # (which Python's isinstance treats as an int) and any hostile
    # int/float subclass are both rejected, matching J4B.3's exact-type
    # discipline.
    if value is None:
        return True
    if type(value) not in (int, float):
        return False
    return math.isfinite(value)


def classify_endpoint_evidence(*, entry_snapshot_present: bool, exit_snapshot_present: bool, entry_value, exit_value) -> str:
    """`entry_value`/`exit_value` are the specific level's (stop or
    target) recorded value in that snapshot — only meaningful when the
    corresponding snapshot is present. Identical non-null endpoint
    values never prove no intermediate edit occurred; different
    non-null values prove only that the endpoints differ, not when or
    how many times a change happened (Gate 1E)."""
    if not entry_snapshot_present and not exit_snapshot_present:
        return ENDPOINT_EVIDENCE_BOTH_SNAPSHOTS_MISSING
    if not entry_snapshot_present:
        return ENDPOINT_EVIDENCE_ENTRY_SNAPSHOT_MISSING
    if not exit_snapshot_present:
        return ENDPOINT_EVIDENCE_EXIT_SNAPSHOT_MISSING

    if not _is_well_formed_endpoint_value(entry_value) or not _is_well_formed_endpoint_value(exit_value):
        return ENDPOINT_EVIDENCE_MALFORMED_VALUE

    if entry_value is None and exit_value is None:
        return ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS
    if entry_value is None and exit_value is not None:
        return ENDPOINT_EVIDENCE_NULL_TO_VALUE
    if entry_value is not None and exit_value is None:
        return ENDPOINT_EVIDENCE_VALUE_TO_NULL
    if entry_value == exit_value:
        return ENDPOINT_EVIDENCE_IDENTICAL_NONNULL_VALUES
    return ENDPOINT_EVIDENCE_DIFFERENT_NONNULL_VALUES


def is_no_level_configured_throughout_eligible(
    *, invariant_version, level_modified_flag, entry_value, exit_value,
) -> bool:
    """Gate 1F — a future NO_LEVEL_CONFIGURED_THROUGHOUT conclusion may
    only ever become eligible for one specific level when ALL of: the
    trade is under a supported governed invariant version, that
    level's per-level flag is reliably FALSE, and both observed
    endpoints are null with no contradictory evidence. Wave A tests
    this eligibility only — it must never be emitted as a report claim
    during Wave A (that belongs to Wave B)."""
    status = classify_level_history_status(invariant_version=invariant_version, level_modified_flag=level_modified_flag)
    if status != LEVEL_HISTORY_STATUS_GOVERNED_UNMODIFIED:
        return False
    endpoint = classify_endpoint_evidence(
        entry_snapshot_present=True, exit_snapshot_present=True, entry_value=entry_value, exit_value=exit_value,
    )
    return endpoint == ENDPOINT_EVIDENCE_NO_LEVEL_VALUES_AT_OBSERVED_ENDPOINTS


# --- Price-basis eligibility (Stage J4C, Gate 1G) ---
PRICE_BASIS_COMPATIBLE = "COMPATIBLE"
PRICE_BASIS_INCOMPATIBLE = "INCOMPATIBLE"
PRICE_BASIS_UNKNOWN = "UNKNOWN"
PRICE_BASIS_CORRUPT_OR_CONTRADICTORY = "CORRUPT_OR_CONTRADICTORY"
PRICE_BASIS_EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"

ALL_PRICE_BASIS_STATUSES = frozenset({
    PRICE_BASIS_COMPATIBLE, PRICE_BASIS_INCOMPATIBLE, PRICE_BASIS_UNKNOWN,
    PRICE_BASIS_CORRUPT_OR_CONTRADICTORY, PRICE_BASIS_EVIDENCE_UNAVAILABLE,
})

# Mirrors price_path_evidence._VALID_ADJUSTMENT_BASES — deliberately
# re-declared rather than imported, so this pure semantic module has no
# import-time dependency on the acquisition-evidence module's shape.
_COMPATIBLE_ADJUSTMENT_BASES = frozenset({"UNADJUSTED"})
_INCOMPATIBLE_ADJUSTMENT_BASES = frozenset({"SPLIT_ADJUSTED", "TOTAL_RETURN_ADJUSTED"})
_UNKNOWN_ADJUSTMENT_BASES = frozenset({"UNKNOWN_ADJUSTMENT"})


def classify_price_basis_eligibility(price_adjustment_basis) -> str:
    """Never fetches an alternate provider or falls back to a current
    market price — this function only classifies evidence already
    acquired. When the basis is not COMPATIBLE, callers must not derive
    a numerical-crossing conclusion from it (Gate 1G)."""
    if price_adjustment_basis is None:
        return PRICE_BASIS_EVIDENCE_UNAVAILABLE
    # Wave A closure correction — exact str only, and whitespace-only
    # rejected as corrupt rather than silently falling through the
    # membership checks below.
    if type(price_adjustment_basis) is not str or not price_adjustment_basis.strip():
        return PRICE_BASIS_CORRUPT_OR_CONTRADICTORY
    if price_adjustment_basis in _COMPATIBLE_ADJUSTMENT_BASES:
        return PRICE_BASIS_COMPATIBLE
    if price_adjustment_basis in _INCOMPATIBLE_ADJUSTMENT_BASES:
        return PRICE_BASIS_INCOMPATIBLE
    if price_adjustment_basis in _UNKNOWN_ADJUSTMENT_BASES:
        return PRICE_BASIS_UNKNOWN
    return PRICE_BASIS_CORRUPT_OR_CONTRADICTORY
