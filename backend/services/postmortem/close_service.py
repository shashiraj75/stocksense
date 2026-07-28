"""
Authoritative paper-trade close service — Trade Postmortem Engine,
Sprint 2.

Problem this solves: before this module, `POST /sell/{trade_id}` was the
only code path that could transition a paper trade from OPEN to CLOSED,
and it did so with three separate, independently-committed statements
under the connection pool's `autocommit=True` connections (no explicit
`with conn.transaction():` wrapped the trade UPDATE and the portfolio
cash UPDATE together) — a crash between them could leave a closed trade
whose cash was never credited. Nothing durable was written about *how*
the trade closed beyond the four flat columns already on `paper_trades`,
and nothing durably recorded that a postmortem report should eventually
be generated for it.

This module is the one authoritative place a paper trade is closed. It:

1. Loads and row-locks the trade (`SELECT ... FOR UPDATE`) inside one
   transaction, so a concurrent close attempt on the same trade serializes
   on PostgreSQL's own row lock rather than racing in application code.
2. Validates ownership, open status, and finite/positive inputs.
3. Computes realized P&L through the SAME shared functions every other
   caller in this codebase already uses
   (`services.paper_trade_math.compute_realized_pnl_abs/_pct`) — this
   module never recomputes P&L independently.
4. Updates the trade's `status`/`exit_price`/`closed_at`/`exit_reason`,
   guarded by `WHERE status = 'OPEN'` as a second, defense-in-depth
   duplicate-close guard.
5. Writes one immutable `paper_trade_exit_snapshot` row
   (services.postmortem.exit_snapshot).
6. Writes one idempotent `paper_trade_postmortem_outbox` row requesting
   report generation.
7. Commits all of the above atomically — one transaction, all-or-nothing.

All existing manual, stop-loss, and target close paths in
`api/routers/paper_trading.py` delegate to `close_paper_trade` (see
`paper_sell`). Portfolio reset is a documented, separate administrative
path (Sprint 2, Stage 11) — it does not go through this service, because
reset's existing, already-shipped contract is DELETION of open trades'
rows entirely, not a close event with a financial outcome; see
`reset_portfolio`'s own comments for why deletion (not administrative
closure) remains the established behavior this sprint does not change.

Does not touch cash crediting — that remains the caller's responsibility
(the caller reads `CloseResult.proceeds` and updates
`paper_portfolio` itself, inside the SAME transaction this function
already opened, by passing its own `conn`). This keeps the service
market-agnostic (it never needs to know which portfolio column a given
market's cash lives in) while still guaranteeing atomicity, since the
caller's cash UPDATE executes while still inside this function's
`with conn.transaction():` block (see `close_paper_trade`'s docstring
below for the exact call-site pattern).
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from services.paper_trade_math import compute_realized_pnl_abs, compute_realized_pnl_pct
from services.postmortem.deterministic import CALCULATION_VERSION
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION
from services.postmortem.exit_snapshot import CloseExitMechanism, ExitSnapshot, build_exit_snapshot

# Sprint 2, Stage 18 — structured observability events. Every log call
# below carries only a stable event tag plus trade_id/outbox_id (never a
# price, P&L figure, symbol, evidence value, or raw exception message —
# the same privacy-safe convention paper_trading.py's own postmortem
# logging already follows).
log = logging.getLogger(__name__)

# Independent of ExitSnapshot's own EXIT_SNAPSHOT_SCHEMA_VERSION — this is
# the daily-postmortem-response schema version the outbox requests a
# report be generated against. Bumped only when the API JSON *shape*
# changes, mirroring PostmortemResponse's own schema_version convention.
REPORT_SCHEMA_VERSION = "1.0.0"

_SUPPORTED_MARKETS = frozenset({"IN", "US"})

_SELL_EXIT_REASON_TO_MECHANISM: dict[str | None, CloseExitMechanism] = {
    "MANUAL": CloseExitMechanism.MANUAL,
    "STOP_LOSS": CloseExitMechanism.STOP_LOSS,
    "TARGET_HIT": CloseExitMechanism.TARGET_HIT,
    None: CloseExitMechanism.MANUAL,
}


def sell_exit_reason_to_mechanism(exit_reason: str | None) -> CloseExitMechanism:
    """The one translation point between SellRequest.exit_reason's
    existing Literal["STOP_LOSS","TARGET_HIT","MANUAL"] | None contract
    (unchanged by this sprint) and the wider CloseExitMechanism vocabulary
    this service understands. An unrecognized raw value (which the
    Pydantic Literal already prevents for /sell today, but a future
    caller might not) maps to UNKNOWN rather than raising — never guessed
    into one of the known mechanisms."""
    return _SELL_EXIT_REASON_TO_MECHANISM.get(exit_reason, CloseExitMechanism.UNKNOWN)


class CloseServiceError(Exception):
    """Base class for close_paper_trade's typed failure modes — callers
    (the API router) catch these specifically and map each to the exact
    HTTP status/detail the existing endpoint contract already returns,
    never a generic 500."""


class TradeNotFoundError(CloseServiceError):
    def __init__(self, trade_id: int):
        super().__init__(f"trade {trade_id} not found")
        self.trade_id = trade_id


class TradeNotOwnedError(CloseServiceError):
    def __init__(self, trade_id: int):
        super().__init__(f"trade {trade_id} is not owned by the requesting user")
        self.trade_id = trade_id


class TradeAlreadyClosedError(CloseServiceError):
    def __init__(self, trade_id: int):
        super().__init__(f"trade {trade_id} is already closed")
        self.trade_id = trade_id


class CloseValidationError(CloseServiceError):
    pass


class UnsupportedMarketError(CloseServiceError):
    def __init__(self, market: str):
        super().__init__(f"unsupported market: {market!r}")
        self.market = market


@dataclass(frozen=True)
class CloseResult:
    trade_id: int
    user_id: str
    symbol: str
    market: str
    quantity: int
    entry_price: float
    exit_price: float
    realized_pnl_abs: float | None
    realized_pnl_pct: float | None
    proceeds: float
    closed_at: datetime
    exit_snapshot: ExitSnapshot
    outbox_id: int
    outbox_created: bool


def _is_finite_positive(value) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


_EXIT_SNAPSHOT_COLUMNS = (
    "paper_trade_id, user_id, symbol, market, exit_snapshot_schema_version, "
    "financial_outcome, closure_classification, exit_mechanism, exit_mechanism_raw, "
    "exit_price, exit_quantity, closed_at, "
    "final_stop_loss, final_target_price, trailing_stop_level, time_exit_rule, market_close_rule, "
    "management_mode, levels_modified_after_entry, "
    "source_request_id, trigger_observation_timestamp, trigger_observation_price, "
    "trigger_timing_verification, source_metadata"
)


def _insert_exit_snapshot(conn, snapshot: ExitSnapshot) -> None:
    import json as _json
    conn.execute(
        f"""INSERT INTO paper_trade_exit_snapshot ({_EXIT_SNAPSHOT_COLUMNS})
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            snapshot.paper_trade_id, snapshot.user_id, snapshot.symbol, snapshot.market,
            snapshot.exit_snapshot_schema_version,
            snapshot.financial_outcome, snapshot.closure_classification,
            snapshot.exit_mechanism, snapshot.exit_mechanism_raw,
            snapshot.exit_price, snapshot.exit_quantity, snapshot.closed_at,
            snapshot.final_stop_loss, snapshot.final_target_price, snapshot.trailing_stop_level,
            snapshot.time_exit_rule, snapshot.market_close_rule,
            snapshot.management_mode, snapshot.levels_modified_after_entry,
            snapshot.source_request_id, snapshot.trigger_observation_timestamp,
            snapshot.trigger_observation_price, snapshot.trigger_timing_verification,
            _json.dumps(snapshot.source_metadata) if snapshot.source_metadata is not None else None,
        ),
    )


def _insert_outbox_record(conn, *, trade_id: int, user_id: str, source_request_id: str | None) -> tuple[int, bool]:
    """Idempotent insertion — `ON CONFLICT DO NOTHING` on the
    (paper_trade_id, requested_report_schema_version,
    requested_calculation_version, requested_rules_version) unique index
    means a duplicate outbox-creation attempt for the identical logical
    generation request is a safe no-op, never a duplicate row. Returns
    (id, created) so the caller can distinguish "this call created the
    row" from "a row already existed" without a second query in the
    common case."""
    row = conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
               (paper_trade_id, user_id, requested_report_schema_version,
                requested_calculation_version, requested_rules_version, source_request_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (paper_trade_id, requested_report_schema_version,
                        requested_calculation_version, requested_rules_version)
           DO NOTHING
           RETURNING id""",
        (trade_id, user_id, REPORT_SCHEMA_VERSION, CALCULATION_VERSION, ATTRIBUTION_RULES_VERSION, source_request_id),
    ).fetchone()
    if row is not None:
        return row[0], True
    existing = conn.execute(
        """SELECT id FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND requested_report_schema_version = %s
             AND requested_calculation_version = %s AND requested_rules_version = %s""",
        (trade_id, REPORT_SCHEMA_VERSION, CALCULATION_VERSION, ATTRIBUTION_RULES_VERSION),
    ).fetchone()
    return existing[0], False


def close_paper_trade(
    conn,
    *,
    user_id: str,
    trade_id: int,
    exit_price: float,
    exit_mechanism: CloseExitMechanism,
    exit_mechanism_raw: str | None,
    closed_at: datetime | None = None,
    source_request_id: str | None = None,
    exit_metadata: dict | None = None,
) -> CloseResult:
    """The one authoritative close path. `conn` is an already-acquired
    connection from the caller's own pool (this module has no pool of its
    own — see module docstring for why); the caller is responsible for
    crediting cash to the correct market's portfolio column INSIDE the
    same `with conn.transaction():` this function opens, by doing so
    immediately after calling this function but before this function
    returns control past its own `with` block — in practice this means
    callers should structure code as:

        with conn.transaction():
            result = close_paper_trade(conn, ...)
            conn.execute("UPDATE paper_portfolio SET ... WHERE user_id = %s", ...)

    rather than calling this function inside its own separate
    transaction — see `paper_sell`'s actual call site for the real
    pattern used in this codebase, since `close_paper_trade` itself opens
    a NESTED transaction context (psycopg's `conn.transaction()` supports
    nesting via savepoints), keeping this function callable both as the
    sole write in a transaction and as one step inside a larger one.

    Raises a CloseServiceError subclass for every documented failure
    mode; never raises a bare Exception for an expected condition.
    """
    if not _is_finite_positive(exit_price):
        raise CloseValidationError(f"exit_price must be a finite positive number, got {exit_price!r}")

    with conn.transaction():
        row = conn.execute(
            """SELECT user_id, symbol, quantity, entry_price, status, market,
                      stop_loss, target_price, trade_management_mode
               FROM paper_trades WHERE id = %s FOR UPDATE""",
            (trade_id,),
        ).fetchone()
        if row is None:
            raise TradeNotFoundError(trade_id)

        owner, symbol, qty, entry_price, status, market, stop_loss, target_price, mgmt_mode = row

        if owner != user_id:
            raise TradeNotOwnedError(trade_id)
        if status != "OPEN":
            log.info("[trade_close_duplicate] trade_id=%s reason=%s", trade_id, "already_closed_on_read")
            raise TradeAlreadyClosedError(trade_id)
        if market not in _SUPPORTED_MARKETS:
            raise UnsupportedMarketError(market)
        if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
            raise CloseValidationError(f"trade {trade_id} has an invalid stored quantity: {qty!r}")

        log.info("[trade_close_started] trade_id=%s", trade_id)
        realized_pnl_abs = compute_realized_pnl_abs(entry_price, exit_price, qty)
        realized_pnl_pct = compute_realized_pnl_pct(entry_price, exit_price)
        actual_closed_at = closed_at or datetime.now(timezone.utc)
        proceeds = round(exit_price * qty, 2)

        # WHERE ... AND status = 'OPEN' — defense-in-depth beyond the row
        # lock above: proves at the exact moment of write that no other
        # transaction closed this trade first. Under a real row lock this
        # should never fail to match, but is kept as a hard guarantee
        # rather than trusting the lock alone.
        updated = conn.execute(
            """UPDATE paper_trades SET exit_price = %s, status = 'CLOSED', closed_at = %s, exit_reason = %s
               WHERE id = %s AND status = 'OPEN' RETURNING id""",
            (exit_price, actual_closed_at, exit_mechanism_raw, trade_id),
        ).fetchone()
        if updated is None:
            log.info("[trade_close_duplicate] trade_id=%s reason=%s", trade_id, "already_closed_at_write")
            raise TradeAlreadyClosedError(trade_id)

        snapshot = build_exit_snapshot(
            paper_trade_id=trade_id,
            user_id=user_id,
            symbol=symbol,
            market=market,
            exit_mechanism=exit_mechanism,
            exit_mechanism_raw=exit_mechanism_raw,
            exit_price=exit_price,
            exit_quantity=qty,
            closed_at=actual_closed_at,
            realized_pnl_abs=realized_pnl_abs,
            final_stop_loss=stop_loss,
            final_target_price=target_price,
            management_mode=mgmt_mode,
            source_request_id=source_request_id,
            source_metadata=exit_metadata,
        )
        _insert_exit_snapshot(conn, snapshot)
        log.info("[exit_snapshot_created] trade_id=%s", trade_id)

        outbox_id, outbox_created = _insert_outbox_record(
            conn, trade_id=trade_id, user_id=user_id, source_request_id=source_request_id
        )
        if outbox_created:
            log.info("[postmortem_outbox_created] trade_id=%s outbox_id=%s", trade_id, outbox_id)

    log.info("[trade_close_completed] trade_id=%s outbox_id=%s", trade_id, outbox_id)
    return CloseResult(
        trade_id=trade_id,
        user_id=user_id,
        symbol=symbol,
        market=market,
        quantity=qty,
        entry_price=entry_price,
        exit_price=exit_price,
        realized_pnl_abs=realized_pnl_abs,
        realized_pnl_pct=realized_pnl_pct if realized_pnl_pct is not None else 0,
        proceeds=proceeds,
        closed_at=actual_closed_at,
        exit_snapshot=snapshot,
        outbox_id=outbox_id,
        outbox_created=outbox_created,
    )
