"""
Immutable exit-evidence snapshot — Trade Postmortem Engine, Sprint 2.

Problem this solves: Sprint 1's evidence-attribution engine can only
reason about what was actually captured at close time — but until this
module, nothing captured a durable, structured record of a trade's close
beyond the four flat columns already on `paper_trades` (exit_price,
closed_at, exit_reason, status). This module is the exit-time mirror of
`entry_snapshot.py`: one immutable row per close event, written once
inside the same transaction as the close itself (see close_service.py),
never UPDATEd afterward.

Two separate classification axes, deliberately never conflated (Sprint 2,
Stage 3):

- `financial_outcome` (WIN/LOSS/BREAKEVEN/INDETERMINATE) — reuses
  services.postmortem.deterministic.Outcome exactly; this module never
  recomputes it independently.
- `closure_classification` (TRADING_EXIT/SYSTEM_EXIT/
  ADMINISTRATIVE_CLOSE/RESET_CLOSE/UNKNOWN_CLOSE) — what KIND of event
  closed the trade. A profitable RESET_CLOSE must never be presented as
  an ordinary trading win without also exposing this field; a caller
  that only reads `financial_outcome` would otherwise conflate "the
  portfolio was reset" with "the trader made a good call."

`exit_mechanism` is a wider vocabulary than
`deterministic.ExitMechanism` (which only distinguishes TARGET_HIT/
STOP_LOSS/MANUAL/UNKNOWN for Phase 1's own narrower purposes) —
extensible to future mechanisms (trailing stop, time-based, market-close,
risk-rule) per Sprint 2's own explicit requirement, without touching
Phase 1's existing, already-shipped enum or its API contract.

Trust boundary, unchanged from entry_snapshot.py's own: a browser-
detected stop-loss/target close is never anything more than
CLIENT_REPORTED_UNVERIFIED — this module has no path to promote it to
SERVER_VERIFIED, because no genuine backend-observed trigger evidence
exists anywhere in this codebase today (auto-close detection happens in
the frontend's own useEffect, which then calls POST /sell itself).
"""

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from services.postmortem.deterministic import Outcome

EXIT_SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class CloseExitMechanism(str, Enum):
    MANUAL = "MANUAL"
    STOP_LOSS = "STOP_LOSS"
    TARGET_HIT = "TARGET_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_BASED = "TIME_BASED"
    MARKET_CLOSE = "MARKET_CLOSE"
    RISK_RULE = "RISK_RULE"
    ADMINISTRATIVE = "ADMINISTRATIVE"
    RESET = "RESET"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ClosureClassification(str, Enum):
    TRADING_EXIT = "TRADING_EXIT"
    SYSTEM_EXIT = "SYSTEM_EXIT"
    ADMINISTRATIVE_CLOSE = "ADMINISTRATIVE_CLOSE"
    RESET_CLOSE = "RESET_CLOSE"
    UNKNOWN_CLOSE = "UNKNOWN_CLOSE"


class TriggerTimingVerification(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CLIENT_REPORTED_UNVERIFIED = "CLIENT_REPORTED_UNVERIFIED"
    # Reserved for a future stage that adds real server-side trigger
    # detection — this module never produces it today, mirroring
    # AutoCloseTimingEvidence.SERVER_VERIFIED's identical reservation in
    # deterministic.py.
    SERVER_VERIFIED = "SERVER_VERIFIED"


# Deterministic, explicit mapping — never inferred ad hoc at each call
# site. A mechanism not in this map is a programming error (caught by
# _classify_closure below raising rather than silently defaulting).
_MECHANISM_TO_CLOSURE: dict[CloseExitMechanism, ClosureClassification] = {
    CloseExitMechanism.MANUAL: ClosureClassification.TRADING_EXIT,
    CloseExitMechanism.STOP_LOSS: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.TARGET_HIT: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.TRAILING_STOP: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.TIME_BASED: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.MARKET_CLOSE: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.RISK_RULE: ClosureClassification.SYSTEM_EXIT,
    CloseExitMechanism.ADMINISTRATIVE: ClosureClassification.ADMINISTRATIVE_CLOSE,
    CloseExitMechanism.RESET: ClosureClassification.RESET_CLOSE,
    CloseExitMechanism.OTHER: ClosureClassification.UNKNOWN_CLOSE,
    CloseExitMechanism.UNKNOWN: ClosureClassification.UNKNOWN_CLOSE,
}

# Mechanisms whose trigger is browser-detected, never backend-observed —
# see module docstring's trust-boundary note. Any mechanism NOT in this
# set gets NOT_APPLICABLE (a manual click has no "trigger" to verify at
# all — the user's own request IS the event).
_BROWSER_DETECTED_MECHANISMS = frozenset({
    CloseExitMechanism.STOP_LOSS, CloseExitMechanism.TARGET_HIT,
    CloseExitMechanism.TRAILING_STOP, CloseExitMechanism.TIME_BASED,
    CloseExitMechanism.MARKET_CLOSE, CloseExitMechanism.RISK_RULE,
})


def classify_closure(mechanism: CloseExitMechanism) -> ClosureClassification:
    if mechanism not in _MECHANISM_TO_CLOSURE:
        raise ValueError(f"no closure classification mapping defined for exit mechanism {mechanism!r}")
    return _MECHANISM_TO_CLOSURE[mechanism]


def classify_trigger_timing_verification(mechanism: CloseExitMechanism) -> TriggerTimingVerification:
    if mechanism in _BROWSER_DETECTED_MECHANISMS:
        return TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED
    return TriggerTimingVerification.NOT_APPLICABLE


def _is_finite_positive(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def classify_financial_outcome(realized_pnl_abs: float | None) -> Outcome:
    """Reuses the exact same money-rounded-2dp breakeven convention as
    deterministic.compute_postmortem — never redefined independently.
    Callers pass an ALREADY-ROUNDED realized_pnl_abs (from
    paper_trade_math.compute_realized_pnl_abs); this function only
    classifies the sign, it never computes P&L itself."""
    if realized_pnl_abs is None:
        return Outcome.INDETERMINATE
    if realized_pnl_abs > 0:
        return Outcome.WIN
    if realized_pnl_abs < 0:
        return Outcome.LOSS
    return Outcome.BREAKEVEN


@dataclass(frozen=True)
class ExitSnapshot:
    """The immutable, persisted shape of one exit-evidence snapshot row.
    Field order/names mirror `paper_trade_exit_snapshot`'s columns."""

    paper_trade_id: int
    user_id: str
    symbol: str
    market: str
    exit_snapshot_schema_version: str

    financial_outcome: str
    closure_classification: str
    exit_mechanism: str
    exit_mechanism_raw: str | None

    exit_price: float
    exit_quantity: int
    closed_at: datetime

    final_stop_loss: float | None
    final_target_price: float | None
    trailing_stop_level: float | None
    time_exit_rule: str | None
    market_close_rule: str | None
    management_mode: str
    levels_modified_after_entry: bool | None
    level_history_contract_version: str | None
    final_stop_modified_after_entry: bool | None
    final_target_modified_after_entry: bool | None

    source_request_id: str | None
    trigger_observation_timestamp: datetime | None
    trigger_observation_price: float | None
    trigger_timing_verification: str

    source_metadata: dict | None


def build_exit_snapshot(
    *,
    paper_trade_id: int,
    user_id: str,
    symbol: str,
    market: str,
    exit_mechanism: CloseExitMechanism,
    exit_mechanism_raw: str | None,
    exit_price: float,
    exit_quantity: int,
    closed_at: datetime,
    realized_pnl_abs: float | None,
    final_stop_loss: float | None = None,
    final_target_price: float | None = None,
    trailing_stop_level: float | None = None,
    time_exit_rule: str | None = None,
    market_close_rule: str | None = None,
    management_mode: str,
    levels_modified_after_entry: bool | None = None,
    level_history_contract_version: str | None = None,
    final_stop_modified_after_entry: bool | None = None,
    final_target_modified_after_entry: bool | None = None,
    source_request_id: str | None = None,
    trigger_observation_timestamp: datetime | None = None,
    trigger_observation_price: float | None = None,
    source_metadata: dict | None = None,
) -> ExitSnapshot:
    """Pure function: construct the immutable exit snapshot to be
    persisted alongside a trade close. No I/O — the caller
    (close_service.py) is responsible for atomic persistence within the
    same transaction as the paper_trades close UPDATE.

    Raises ValueError for non-finite/non-positive exit_price or
    non-positive exit_quantity — the same "never let a NaN/Infinity value
    reach persisted evidence" guarantee Sprint 1's evidence.py enforces
    for claims, applied here at the point of construction rather than
    only at the database CHECK constraint (defense in depth, not a
    substitute for it)."""
    if not _is_finite_positive(exit_price):
        raise ValueError(f"exit_price must be a finite positive number, got {exit_price!r}")
    if not isinstance(exit_quantity, int) or isinstance(exit_quantity, bool) or exit_quantity <= 0:
        raise ValueError(f"exit_quantity must be a positive integer, got {exit_quantity!r}")

    return ExitSnapshot(
        paper_trade_id=paper_trade_id,
        user_id=user_id,
        symbol=symbol,
        market=market,
        exit_snapshot_schema_version=EXIT_SNAPSHOT_SCHEMA_VERSION,
        financial_outcome=classify_financial_outcome(realized_pnl_abs).value,
        closure_classification=classify_closure(exit_mechanism).value,
        exit_mechanism=exit_mechanism.value,
        exit_mechanism_raw=exit_mechanism_raw,
        exit_price=exit_price,
        exit_quantity=exit_quantity,
        closed_at=closed_at,
        final_stop_loss=final_stop_loss,
        final_target_price=final_target_price,
        trailing_stop_level=trailing_stop_level,
        time_exit_rule=time_exit_rule,
        market_close_rule=market_close_rule,
        management_mode=management_mode,
        levels_modified_after_entry=levels_modified_after_entry,
        level_history_contract_version=level_history_contract_version,
        final_stop_modified_after_entry=final_stop_modified_after_entry,
        final_target_modified_after_entry=final_target_modified_after_entry,
        source_request_id=source_request_id,
        trigger_observation_timestamp=trigger_observation_timestamp,
        trigger_observation_price=trigger_observation_price,
        trigger_timing_verification=classify_trigger_timing_verification(exit_mechanism).value,
        source_metadata=source_metadata,
    )
