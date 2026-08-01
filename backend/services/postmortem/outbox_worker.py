"""
Trade Postmortem Sprint 3A, Wave B Stage J4F — bounded background
recovery worker for the current (1.2.0) governed-report outbox
identity.

Flag-gated (TRADE_POSTMORTEM_PRICE_PATH_ENABLED, the existing flag — no
new flag activation this wave) and default-disabled. Started/stopped
from api/main.py's lifespan, matching that module's existing
asyncio.create_task background-loop pattern.

Multi-replica-safe via a genuine PostgreSQL SELECT ... FOR UPDATE SKIP
LOCKED claim (see claim_next_current_outbox_batch below) — never a
process-local lock. Deliberately avoids the previously-discovered
stale-CTE/EvalPlanQual double-claim defect class (outbox.py's own
claim_next_attempt docstring documents that history): the claim query
here is a single UPDATE whose WHERE clause references the target table
directly, with the claimable-row selection expressed as a `SELECT ...
FOR UPDATE SKIP LOCKED` subquery evaluated (and its locks taken) BEFORE
the UPDATE proceeds — this is the standard, safe PostgreSQL batch-claim
pattern, distinct from the CTE-based approach that let EvalPlanQual
silently skip the guard re-check after a lock wait.

Uses current_report_generation.process_current_report — the SAME
shared orchestrator close-time best-effort generation and the explicit
POST recovery endpoint already call — never a fourth, independently-
implemented generation flow.
"""
import asyncio
import logging

from services.postmortem import outbox as outbox_ops
from services.postmortem.current_report_generation import (
    CURRENT_REPORT_FAILED_TERMINAL,
    CURRENT_REPORT_SCHEMA_VERSION,
    current_target_identity,
    process_current_report,
)
from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
from services.postmortem.deterministic import CALCULATION_VERSION

log = logging.getLogger(__name__)

# Named, explicit constants — never a bare magic number inline.
POLL_INTERVAL_SECONDS = 30
CLAIM_BATCH_SIZE = 10
# Same lease window outbox.py's own per-trade claim uses, so a row
# claimed globally and a row claimed per-trade never disagree about how
# long a lease is valid.
WORKER_LEASE_DURATION_SECONDS = outbox_ops.LEASE_DURATION_SECONDS

_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None


def claim_next_current_outbox_batch(
    conn, *, claimant: str, limit: int = CLAIM_BATCH_SIZE,
    schema_version: str | None = None, calculation_version: str | None = None, rules_version: str | None = None,
) -> list[dict]:
    """Global (cross-user) atomic claim for the current (1.2.0) outbox
    identity. Filters by the EXACT requested version triple — a worker
    binary only ever claims rows for the identity it knows how to
    generate, never an arbitrary pending row regardless of version.

    Returns a list of dicts (never an OutboxRow instance reused across
    users — each row carries its OWN paper_trade_id/user_id, so a
    background loop iterating this batch never leaks one user's context
    into another's)."""
    if schema_version is None or calculation_version is None or rules_version is None:
        schema_version, calculation_version, rules_version = current_target_identity(
            base_calculation_version=CALCULATION_VERSION,
            numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION,
            source_version=SOURCE_VERSION,
        )
    rows = conn.execute(
        """UPDATE paper_trade_postmortem_outbox o
           SET status = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN 'FAILED_TERMINAL' ELSE 'GENERATING' END,
               attempt_count = o.attempt_count + 1,
               last_attempt_at = now(),
               claimed_at = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN o.claimed_at ELSE now() END,
               lease_expires_at = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN NULL
                                        ELSE now() + make_interval(secs => %(lease_seconds)s) END,
               claimed_by = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN NULL ELSE %(claimant)s END,
               completed_at = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN now() ELSE o.completed_at END,
               last_error_code = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s) THEN 'MAX_ATTEMPTS_EXCEEDED' ELSE o.last_error_code END,
               last_error_summary = CASE WHEN (o.attempt_count + 1 > %(max_attempts)s)
                                          THEN 'exceeded max attempts before terminal' ELSE o.last_error_summary END
           WHERE o.id IN (
               SELECT id FROM paper_trade_postmortem_outbox
               WHERE requested_report_schema_version = %(schema_version)s
                 AND requested_calculation_version = %(calculation_version)s
                 AND requested_rules_version = %(rules_version)s
                 AND (
                     status = 'PENDING'
                     OR (status = 'FAILED_RETRYABLE' AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
                     OR (status = 'GENERATING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now())
                 )
               ORDER BY next_attempt_at NULLS FIRST, created_at
               LIMIT %(limit)s
               FOR UPDATE SKIP LOCKED
           )
           RETURNING o.id, o.paper_trade_id, o.user_id, o.status""",
        {
            "max_attempts": outbox_ops.MAX_ATTEMPTS_BEFORE_TERMINAL, "lease_seconds": WORKER_LEASE_DURATION_SECONDS,
            "claimant": claimant, "schema_version": schema_version, "calculation_version": calculation_version,
            "rules_version": rules_version, "limit": limit,
        },
    ).fetchall()
    return [
        {"outbox_id": r[0], "trade_id": r[1], "user_id": r[2], "status": r[3]}
        for r in rows
    ]


async def _process_claimed_row(row: dict, claimant: str, *, market_tzinfo, market_timezone_name, conn_factory) -> None:
    """Per-row isolation: an exception processing one claimed row must
    never abort the rest of the batch."""
    if row["status"] == "FAILED_TERMINAL":
        return
    try:
        await asyncio.to_thread(
            process_current_report,
            conn_factory, trade_id=row["trade_id"], user_id=row["user_id"],
            market_tzinfo=market_tzinfo, market_timezone_name=market_timezone_name,
            outbox_id=row["outbox_id"], claimed_by=claimant,
        )
    except Exception:
        log.warning("[outbox_worker] row processing failed outbox_id=%s", row["outbox_id"])


async def _poll_once(conn_factory, *, market_tzinfo, market_timezone_name, batch_size: int = CLAIM_BATCH_SIZE) -> int:
    claimant = outbox_ops.new_claimant_token()
    try:
        with conn_factory() as conn:
            batch = claim_next_current_outbox_batch(conn, claimant=claimant, limit=batch_size)
    except Exception:
        log.warning("[outbox_worker] claim batch failed — database outage or transient error, backing off")
        return 0

    for row in batch:
        await _process_claimed_row(
            row, claimant, market_tzinfo=market_tzinfo, market_timezone_name=market_timezone_name,
            conn_factory=conn_factory,
        )
    return len(batch)


async def _worker_loop(conn_factory, *, market_tzinfo_by_market: dict, poll_interval_seconds: int = POLL_INTERVAL_SECONDS) -> None:
    log.info("[outbox_worker] started")
    assert _STOP_EVENT is not None
    try:
        while not _STOP_EVENT.is_set():
            for market, (tzinfo, tz_name) in market_tzinfo_by_market.items():
                if _STOP_EVENT.is_set():
                    break
                try:
                    await _poll_once(conn_factory, market_tzinfo=tzinfo, market_timezone_name=tz_name)
                except Exception:
                    log.warning("[outbox_worker] poll cycle failed for market=%s", market)
            # No database connection is held across this sleep — the
            # claim/process cycle above has already released its
            # connection back to the pool by this point. stop_
            # outbox_worker's own timeout (WORKER_LEASE_DURATION_SECONDS
            # + POLL_INTERVAL_SECONDS) accounts for the fact that a stop
            # request is only recognized at the next loop iteration.
            await asyncio.sleep(poll_interval_seconds)
    finally:
        log.info("[outbox_worker] stopped")


def start_outbox_worker(conn_factory, *, market_tzinfo_by_market: dict, poll_interval_seconds: int = POLL_INTERVAL_SECONDS) -> asyncio.Task | None:
    """Idempotent: calling this twice without an intervening stop_
    never spawns a second concurrent poll loop. Flag-gating is the
    CALLER's responsibility (api/main.py checks TRADE_POSTMORTEM_
    PRICE_PATH_ENABLED before calling this), matching every other
    flag-gated background loop already in that module."""
    global _TASK, _STOP_EVENT
    if _TASK is not None and not _TASK.done():
        return _TASK
    _STOP_EVENT = asyncio.Event()
    _TASK = asyncio.create_task(_worker_loop(conn_factory, market_tzinfo_by_market=market_tzinfo_by_market, poll_interval_seconds=poll_interval_seconds))
    return _TASK


async def stop_outbox_worker() -> None:
    """Graceful shutdown — signals the loop to stop AFTER its current
    in-flight claim/process cycle finishes (never cancels a task mid-
    transaction while holding a lease)."""
    global _TASK, _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _TASK is not None:
        try:
            await asyncio.wait_for(_TASK, timeout=WORKER_LEASE_DURATION_SECONDS + POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            log.warning("[outbox_worker] stop timed out waiting for in-flight cycle")
    _TASK = None
    _STOP_EVENT = None
