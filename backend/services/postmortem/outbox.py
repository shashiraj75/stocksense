"""
Report-generation outbox operations — Trade Postmortem Engine, Sprint 2.

Insertion (idempotent, INSERT ... ON CONFLICT DO NOTHING on the
(paper_trade_id, requested_report_schema_version,
requested_calculation_version, requested_rules_version) unique index)
lives in close_service.py, since it must happen inside the SAME
transaction as the trade close itself. This module holds the operations
that happen AFTER that transaction has committed: claiming a row for
processing, and marking a claimed row terminal — both single atomic
UPDATE statements, so PostgreSQL's own row-level locking (not a
process-local lock) is the entire concurrency guarantee. Two workers (or
two request handlers) racing to claim the same outbox row can never both
succeed, because the second UPDATE's WHERE clause no longer matches once
the first has already flipped `status`.

Architecture decision (Sprint 2, Stage 5): Option B — safe on-request
recovery plus best-effort immediate post-commit processing. No new
scheduler, worker process, or lease-renewal daemon is introduced; no
existing approved worker/lease framework exists in this codebase to
reuse (the closest precedent, daily_picks_jobs' job-claim pattern, is a
long-running batch-job system, not a per-row outbox — reusing it would
be a far larger and riskier change than this sprint's scope). Operational
limitation, stated plainly: a PENDING or FAILED_RETRYABLE row is only
ever revisited when a request actually calls `claim_and_generate` again
(the close request's own best-effort attempt, or a later
POST /postmortem/{trade_id}/generate call, or the existing GET
endpoint's own on-request recovery) — there is no background sweep. A
trade whose report generation failed and whose owner never revisits it
will sit in FAILED_RETRYABLE/PENDING indefinitely. This is a documented,
accepted limitation of the Option B design, not an oversight; a future
sprint may add a bounded periodic sweep once a real worker framework
exists.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_ATTEMPTS_BEFORE_TERMINAL = 5
_RETRY_BACKOFF_SECONDS = 30

_TERMINAL_STATUSES = frozenset({"COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"})
_CLAIMABLE_STATUSES = ("PENDING", "FAILED_RETRYABLE")


@dataclass(frozen=True)
class OutboxRow:
    id: int
    paper_trade_id: int
    user_id: str
    requested_report_schema_version: str
    requested_calculation_version: str
    requested_rules_version: str
    status: str
    attempt_count: int
    source_request_id: str | None


def claim_next_attempt(conn, *, outbox_id: int, user_id: str) -> OutboxRow | None:
    """Atomically claims one outbox row for processing — flips PENDING or
    FAILED_RETRYABLE to GENERATING, incrementing attempt_count, in one
    UPDATE. Returns None if the row doesn't exist, isn't owned by
    `user_id`, or is not currently in a claimable status (already
    GENERATING by a concurrent claimer, or already terminal) — the caller
    must treat None as "nothing to do right now," never as an error."""
    row = conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = 'GENERATING', attempt_count = attempt_count + 1, last_attempt_at = now()
           WHERE id = %s AND user_id = %s AND status = ANY(%s)
           RETURNING id, paper_trade_id, user_id, requested_report_schema_version,
                     requested_calculation_version, requested_rules_version, status, attempt_count,
                     source_request_id""",
        (outbox_id, user_id, list(_CLAIMABLE_STATUSES)),
    ).fetchone()
    if row is None:
        return None
    return OutboxRow(*row)


def find_claimable_outbox_for_trade(conn, *, trade_id: int, user_id: str) -> OutboxRow | None:
    """Looks up the current outbox row for a trade (there is at most one
    per live version triple — see the unique index) without claiming it.
    Used by GET-triggered recovery to decide whether there's anything to
    attempt before calling claim_next_attempt."""
    row = conn.execute(
        """SELECT id, paper_trade_id, user_id, requested_report_schema_version,
                  requested_calculation_version, requested_rules_version, status, attempt_count,
                  source_request_id
           FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND user_id = %s
           ORDER BY created_at DESC LIMIT 1""",
        (trade_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return OutboxRow(*row)


def mark_terminal(conn, *, outbox_id: int, status: str) -> None:
    """status must be one of COMPLETE/LIMITED_EVIDENCE/FAILED_TERMINAL —
    the three statuses this outbox row can permanently settle into.
    Retryable failures use mark_retryable_failure instead."""
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"mark_terminal requires a terminal status, got {status!r}")
    conn.execute(
        "UPDATE paper_trade_postmortem_outbox SET status = %s, completed_at = now() WHERE id = %s",
        (status, outbox_id),
    )


def mark_retryable_failure(conn, *, outbox_id: int, error_code: str, error_summary: str) -> None:
    """Never stores a raw exception message, secret, connection string,
    or report narrative — error_code is a stable machine-readable
    identifier (e.g. "GENERATION_ERROR"), error_summary is a short,
    pre-sanitized human description supplied by the caller, never an
    f-string of the original exception object."""
    conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = 'FAILED_RETRYABLE', last_error_code = %s, last_error_summary = %s,
               next_attempt_at = now() + make_interval(secs => %s)
           WHERE id = %s""",
        (error_code, error_summary, _RETRY_BACKOFF_SECONDS, outbox_id),
    )


def mark_terminal_failure(conn, *, outbox_id: int, error_code: str, error_summary: str) -> None:
    conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = 'FAILED_TERMINAL', completed_at = now(), last_error_code = %s, last_error_summary = %s
           WHERE id = %s""",
        (error_code, error_summary, outbox_id),
    )
