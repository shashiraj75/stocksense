"""
Deterministic closed-trade analytics — Trade Postmortem Engine, Phase 1.

Problem this solves: today, "why did this paper trade end up a win or a
loss" has no server-side answer at all beyond the raw entry/exit prices
already returned by GET /portfolio. This module is the first, purely
deterministic layer of the Trade Postmortem Engine: given a single closed
trade's stored fields, it computes an outcome classification, realized P&L
(via services/paper_trade_math.py — the single authoritative P&L
calculation, never recomputed independently here), holding duration, an
exit-mechanism classification, and an honest evidence-completeness label.

Explicitly out of scope for this module (later, separately approved
phases): AI-generated narrative, causal "what went right/wrong" attribution,
signal-attribution matrices, market/sector/news context, database
persistence or versioning of the result, and anything that requires
evidence this codebase does not yet capture at trade-entry time (see the
provenance columns below — they exist on `paper_trades` but are NULL for
essentially every trade opened through the live product today).

No short-selling is supported anywhere in this codebase — `paper_trades`
has no `direction`/`is_short` column, so every trade is implicitly a long
position (buy low, sell high is a win). There is no direction branch to get
wrong; `target_distance_at_exit_pct`/`stop_distance_at_exit_pct` below are
defined and tested only for this long-only reality and would need a sign
flip if short-selling were ever added.

Auto-close is browser-triggered, not server-verified (see
`api/routers/paper_trading.py` — the stop/target comparison happens in the
frontend's `useEffect`, which then calls POST /sell itself). This module
therefore never asserts `SERVER_VERIFIED` timing for an automatic exit —
see `AutoCloseTimingEvidence` below.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from services.paper_trade_math import compute_realized_pnl_abs, compute_realized_pnl_pct
from services.postmortem.entry_snapshot import EntrySnapshot, classify_evidence_completeness

# Bumped whenever the calculation/classification RULES in this module change
# (rounding convention, breakeven definition, evidence-field set, etc.) — a
# stored/cached postmortem (a later phase's concern; Phase 1 never persists
# anything) tagged with an older version is known to have been computed
# under different rules. Independent of the API response's own schema
# version (see PostmortemResponse.schema_version in the router), which
# tracks the JSON *shape*, not the calculation rules.
CALCULATION_VERSION = "1.1.0"


class Outcome(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    INDETERMINATE = "INDETERMINATE"


class ExitMechanism(str, Enum):
    TARGET_HIT = "TARGET_HIT"
    STOP_LOSS = "STOP_LOSS"
    MANUAL = "MANUAL"
    # Stored `exit_reason` is NULL (legacy trade closed before the column
    # existed, or a manual close whose client never sent exit_reason) or any
    # value this module doesn't recognize — never guessed into one of the
    # three known mechanisms above.
    UNKNOWN = "UNKNOWN"


class AutoCloseTimingEvidence(str, Enum):
    # Manual-mode trades, and any auto-mode trade whose exit wasn't actually
    # an auto-trigger close (e.g. a MANUAL exit_reason on an auto-managed
    # trade) — the auto-close timing question simply doesn't apply.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    # An auto-managed trade that closed via TARGET_HIT/STOP_LOSS. Today this
    # is ALWAYS how such an exit happened — the browser detected the
    # trigger and called POST /sell itself; there is no server-side
    # detection to corroborate the reported closed_at/exit_price against.
    CLIENT_REPORTED_UNVERIFIED = "CLIENT_REPORTED_UNVERIFIED"
    # Reserved for a future phase that adds real server-side trigger
    # detection (e.g. a price-observation table with its own trigger
    # timestamp). This module never returns it — there is no such evidence
    # to verify against today.
    SERVER_VERIFIED = "SERVER_VERIFIED"


class EvidenceCompleteness(str, Enum):
    """Field-PRESENCE only — how many of the recommendation-evidence
    fields (see `VERIFIABLE_EVIDENCE_FIELDS`) are non-NULL for this trade.

    `COMPLETE` means every required Stage 2 entry field is present. It does
    NOT mean the values were independently corroborated — a trade can be
    `COMPLETE` in this sense while every one of its fields is still
    `VerificationLevel.CLIENT_REPORTED` (see
    `entry_snapshot.VerificationLevel`). Presence and trust are reported
    as two entirely separate fields on every API response
    (`evidence_completeness` and `verification_levels`) specifically so a
    caller can never collapse "the client says it filled in everything" into
    "this was verified" — never conflate the two when adding UI or
    narrative copy on top of this."""

    LIMITED = "LIMITED"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


# The explicit set of recommendation/provenance fields this module treats as
# "evidence" for completeness scoring — the Learning Alpha Engine
# remediation Phase 1 columns added to `paper_trades`
# (services/postgres_store.py's SCHEMA_SQL). Every one of these is
# genuinely optional/nullable on the live table; a fresh trade opened
# through the current UI stores NULL for all of them (no client sends them
# yet), which is exactly the LIMITED case below.
#
# Deliberately EXCLUDED from this set:
#   - `signal_override`: hardcoded to NULL by /buy unconditionally today
#     (see paper_trading.py's paper_buy) — it is not trade-specific
#     evidence, it is a not-yet-implemented feature, so including it would
#     make COMPLETE permanently unreachable regardless of what a client
#     actually supplies.
#   - `levels_modified_after_entry`: a post-entry mutation-audit flag, not
#     entry-time recommendation evidence.
EVIDENCE_FIELDS: tuple[str, ...] = (
    "recommendation_source",
    "daily_pick_run_id",
    "daily_pick_rank",
    "recommendation_generated_at",
    "recommendation_reference_price",
    "recommendation_entry_low",
    "recommendation_entry_high",
    "recommendation_original_stop_loss",
    "recommendation_original_target",
    "model_version",
    "execution_slippage_pct",
)

_EXIT_REASON_MAP: dict[str, ExitMechanism] = {
    "TARGET_HIT": ExitMechanism.TARGET_HIT,
    "STOP_LOSS": ExitMechanism.STOP_LOSS,
    "MANUAL": ExitMechanism.MANUAL,
}


def _is_finite_positive(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _is_finite(value: float | None) -> bool:
    return value is not None and isinstance(value, (int, float)) and math.isfinite(value)


@dataclass(frozen=True)
class ClosedTradeRecord:
    """Typed input boundary for `compute_postmortem` — the validated shape
    of one `paper_trades` row. Callers (the API router) are responsible for
    fetching the row and constructing this; this dataclass does not itself
    query the database. Field types mirror the actual nullable/non-nullable
    columns in `services/postgres_store.py`'s SCHEMA_SQL for `paper_trades`
    — nothing here is guessed or assumed beyond what that schema declares.

    `status` must be `"CLOSED"` — `compute_postmortem` raises `ValueError`
    otherwise. Open-trade rejection is the API layer's job (409
    TRADE_NOT_CLOSED), not this function's; an open trade must never reach
    outcome classification, so this is a defensive contract check, not a
    normal-path outcome.
    """

    trade_id: int
    status: str
    symbol: str
    market: str
    quantity: int | None
    entry_price: float | None
    exit_price: float | None
    stop_loss: float | None
    target_price: float | None
    opened_at: datetime | None
    closed_at: datetime | None
    trade_management_mode: str
    exit_reason: str | None
    recommendation_source: str | None = None
    daily_pick_run_id: str | None = None
    daily_pick_rank: int | None = None
    recommendation_generated_at: datetime | None = None
    recommendation_reference_price: float | None = None
    recommendation_entry_low: float | None = None
    recommendation_entry_high: float | None = None
    recommendation_original_stop_loss: float | None = None
    recommendation_original_target: float | None = None
    model_version: str | None = None
    execution_slippage_pct: float | None = None


@dataclass(frozen=True)
class DeterministicPostmortem:
    trade_id: int
    status: str
    outcome: Outcome
    realized_pnl_abs: float | None
    realized_pnl_pct: float | None
    holding_duration_seconds: float | None
    exit_mechanism: ExitMechanism
    exit_mechanism_raw: str | None
    trade_management_mode: str
    auto_close_timing_evidence: AutoCloseTimingEvidence
    evidence_completeness: EvidenceCompleteness
    available_evidence_fields: list[str]
    missing_evidence_fields: list[str]
    target_distance_at_exit_pct: float | None
    stop_distance_at_exit_pct: float | None
    calculation_version: str
    warnings: list[str] = field(default_factory=list)


def _classify_exit_mechanism(exit_reason: str | None) -> ExitMechanism:
    if not isinstance(exit_reason, str):
        return ExitMechanism.UNKNOWN
    return _EXIT_REASON_MAP.get(exit_reason, ExitMechanism.UNKNOWN)


def _classify_auto_close_timing(
    exit_mechanism: ExitMechanism, trade_management_mode: str
) -> AutoCloseTimingEvidence:
    if trade_management_mode == "auto" and exit_mechanism in (
        ExitMechanism.TARGET_HIT,
        ExitMechanism.STOP_LOSS,
    ):
        return AutoCloseTimingEvidence.CLIENT_REPORTED_UNVERIFIED
    return AutoCloseTimingEvidence.NOT_APPLICABLE


def _evidence_completeness(record: ClosedTradeRecord) -> tuple[EvidenceCompleteness, list[str], list[str]]:
    """Legacy (pre-Stage-2) path — evaluated only when no
    `paper_trade_entry_snapshot` row exists for this trade at all. Kept
    byte-for-byte unchanged so any trade created through the old flat
    provenance-field API contract (no `entry_evidence` supplied) — and
    every historical trade predating Stage 2 — is scored exactly as Phase 1
    always scored it. Never used when a snapshot exists; see
    `_evidence_completeness_from_snapshot` below."""
    available = sorted(f for f in EVIDENCE_FIELDS if getattr(record, f) is not None)
    missing = sorted(f for f in EVIDENCE_FIELDS if getattr(record, f) is None)
    if len(available) == 0:
        completeness = EvidenceCompleteness.LIMITED
    elif len(available) == len(EVIDENCE_FIELDS):
        completeness = EvidenceCompleteness.COMPLETE
    else:
        completeness = EvidenceCompleteness.PARTIAL
    return completeness, available, missing


def _evidence_completeness_from_snapshot(
    snapshot: EntrySnapshot,
) -> tuple[EvidenceCompleteness, list[str], list[str]]:
    """Stage 2 path — used whenever a `paper_trade_entry_snapshot` row
    exists for the trade (every trade opened through the current frontend
    has one; see api/routers/paper_trading.py's paper_buy). Delegates to
    `entry_snapshot.classify_evidence_completeness` — the single source of
    truth also used by the Buy response itself, so "COMPLETE" can never
    mean two different things depending on which endpoint asks."""
    completeness_str, available, missing = classify_evidence_completeness(snapshot)
    return EvidenceCompleteness(completeness_str), available, missing


def _distance_at_exit_pct(exit_price: float | None, level: float | None) -> float | None:
    """`(exit_price - level) / level * 100`, rounded to 2dp — long-only
    sign convention (see module docstring): positive means the exit price
    finished ABOVE `level`, negative means it finished BELOW it. Returns
    `None` when either input is missing, non-finite, or `level <= 0`
    (nothing to safely divide by). This is purely a distance measurement —
    it never asserts that a target/stop was actually "crossed"; that claim
    is only made via `exit_mechanism`, which reflects the stored
    `exit_reason` evidence, not a price comparison performed here.
    """
    if not _is_finite(exit_price) or not _is_finite_positive(level):
        return None
    return round((exit_price - level) / level * 100, 2)


def compute_postmortem(
    record: ClosedTradeRecord, snapshot: EntrySnapshot | None = None
) -> DeterministicPostmortem:
    """Pure function: one validated closed-trade record in, one
    deterministic postmortem result out. No I/O, no AI, no randomness — the
    same input always produces the same output (module-level
    `CALCULATION_VERSION` is bumped, not this function's behavior silently
    changed, whenever the rules below change).

    `snapshot` (Stage 2, additive, optional): the trade's immutable entry
    snapshot, when one exists. `None` reproduces Phase 1's exact prior
    behavior byte-for-byte — every existing caller/test that doesn't pass
    this argument is unaffected. When provided, evidence-completeness is
    computed from the snapshot's richer field set instead of the legacy
    11-field `paper_trades`-column check.
    """
    if record.status != "CLOSED":
        raise ValueError(
            f"compute_postmortem requires a CLOSED trade; got status={record.status!r} "
            f"for trade_id={record.trade_id}. Open-trade rejection is the API layer's "
            "responsibility (409 TRADE_NOT_CLOSED), not this function's."
        )

    warnings: list[str] = []

    entry_price = record.entry_price
    exit_price = record.exit_price
    quantity = record.quantity

    entry_valid = _is_finite_positive(entry_price)
    exit_valid = _is_finite_positive(exit_price)
    quantity_valid = quantity is not None and isinstance(quantity, int) and quantity > 0

    if not entry_valid:
        warnings.append("entry_price is missing, zero, negative, or non-finite")
    if not exit_valid:
        warnings.append("exit_price is missing, zero, negative, or non-finite")
    if not quantity_valid:
        warnings.append("quantity is missing, zero, negative, or not an integer")

    pnl_inputs_valid = entry_valid and exit_valid and quantity_valid
    realized_pnl_abs = (
        compute_realized_pnl_abs(entry_price, exit_price, quantity) if pnl_inputs_valid else None
    )
    realized_pnl_pct = (
        compute_realized_pnl_pct(entry_price, exit_price) if pnl_inputs_valid else None
    )

    opened_at = record.opened_at
    closed_at = record.closed_at
    holding_duration_seconds: float | None = None
    if opened_at is None:
        warnings.append("opened_at timestamp is missing")
    if closed_at is None:
        warnings.append("closed_at timestamp is missing")
    if opened_at is not None and closed_at is not None:
        delta_seconds = (closed_at - opened_at).total_seconds()
        if delta_seconds < 0:
            warnings.append("closed_at precedes opened_at (negative holding duration)")
        else:
            holding_duration_seconds = delta_seconds

    # Outcome classification: WIN/LOSS/BREAKEVEN require both P&L inputs and
    # timestamps to be valid — a record can compute a valid P&L but still
    # have a malformed duration (or vice versa), and either malformation is
    # enough to make the "final outcome" INDETERMINATE rather than silently
    # reporting a partial result as if it were fully reliable. Breakeven is
    # defined against the already-money-rounded (2dp) realized_pnl_abs —
    # the same rounding convention _trade_row_to_dict/paper_sell use
    # elsewhere — so a sub-cent floating-point residue (e.g. 1e-13 from a
    # price/quantity multiplication) never spuriously classifies a trade
    # that is economically breakeven as a WIN or LOSS.
    is_reliable = pnl_inputs_valid and holding_duration_seconds is not None
    if not is_reliable:
        outcome = Outcome.INDETERMINATE
    elif realized_pnl_abs > 0:
        outcome = Outcome.WIN
    elif realized_pnl_abs < 0:
        outcome = Outcome.LOSS
    else:
        outcome = Outcome.BREAKEVEN

    exit_mechanism = _classify_exit_mechanism(record.exit_reason)
    auto_close_timing_evidence = _classify_auto_close_timing(
        exit_mechanism, record.trade_management_mode
    )
    if snapshot is not None:
        evidence_completeness, available_fields, missing_fields = _evidence_completeness_from_snapshot(snapshot)
    else:
        evidence_completeness, available_fields, missing_fields = _evidence_completeness(record)

    target_distance_at_exit_pct = _distance_at_exit_pct(exit_price, record.target_price)
    stop_distance_at_exit_pct = _distance_at_exit_pct(exit_price, record.stop_loss)

    return DeterministicPostmortem(
        trade_id=record.trade_id,
        status=record.status,
        outcome=outcome,
        realized_pnl_abs=realized_pnl_abs,
        realized_pnl_pct=realized_pnl_pct,
        holding_duration_seconds=holding_duration_seconds,
        exit_mechanism=exit_mechanism,
        # Stage 0 audit fix: `exit_reason` is a TEXT column, so a real
        # Postgres row can never hand this a non-string — but the typed
        # boundary previously passed record.exit_reason through verbatim
        # regardless of type, which would surface a raw int/other object to
        # the API's `str | None` response field if a caller ever constructed
        # a ClosedTradeRecord from something other than a real DB row (e.g.
        # a future test fixture or a different data source). Sanitized here
        # so "the original sanitized stored value where safe" is actually
        # sanitized, not just documented as such.
        exit_mechanism_raw=record.exit_reason if isinstance(record.exit_reason, str) else None,
        trade_management_mode=record.trade_management_mode,
        auto_close_timing_evidence=auto_close_timing_evidence,
        evidence_completeness=evidence_completeness,
        available_evidence_fields=available_fields,
        missing_evidence_fields=missing_fields,
        target_distance_at_exit_pct=target_distance_at_exit_pct,
        stop_distance_at_exit_pct=stop_distance_at_exit_pct,
        calculation_version=CALCULATION_VERSION,
        warnings=warnings,
    )
