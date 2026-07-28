"""
Report-generation outbox operations — Trade Postmortem Engine, Sprint 2.

Insertion (idempotent, INSERT ... ON CONFLICT DO NOTHING on the
(paper_trade_id, requested_report_schema_version,
requested_calculation_version, requested_rules_version) unique index)
lives in close_service.py, since it must happen inside the SAME
transaction as the trade close itself. This module holds everything that
happens AFTER that transaction has committed: claiming a row for
processing, and marking a claimed row terminal or retryable.

Architecture decision (Sprint 2, Stage 5): Option B — safe on-request
recovery plus best-effort immediate post-commit processing. No new
scheduler, worker process, or lease-renewal daemon is introduced; no
existing approved worker/lease framework exists in this codebase to
reuse (the closest precedent, daily_picks_jobs' job-claim pattern, is a
long-running batch-job system, not a per-row outbox — reusing it would
be a far larger and riskier change than this sprint's scope). Operational
limitation, stated plainly: a PENDING or FAILED_RETRYABLE row is only
ever revisited when a request actually calls claim_next_attempt again
(the close request's own best-effort attempt, or a later
POST /postmortem/{trade_id}/generate call) — there is no background
sweep. A trade whose report generation failed and whose owner never
revisits it will sit in FAILED_RETRYABLE/PENDING indefinitely until its
attempt limit is reached. This is a documented, accepted limitation of
the Option B design, not an oversight; a future sprint may add a bounded
periodic sweep once a real worker framework exists.

Correction phase (post-PR-#33-CI-failure): the original design's
GENERATING window was unbounded — a process kill between claim and
terminal-mark left a row permanently stuck (unclaimable, since the claim
UPDATE only ever matched PENDING/FAILED_RETRYABLE). This module now
implements a genuine, database-backed lease: `claim_next_attempt` also
reclaims a GENERATING row whose `lease_expires_at` has passed, in the
SAME atomic UPDATE as the PENDING/FAILED_RETRYABLE cases — PostgreSQL's
own row-level locking (never a process-local lock) is still the entire
concurrency guarantee, so two claimers racing for the same expired lease
can never both win. Every terminal/retryable mark now requires the
caller's `claimed_by` token to still match the row's current lease
holder — if a stale worker's lease already expired and was reclaimed by
someone else, its mark attempt matches zero rows and returns False
rather than silently overwriting the new claimant's result.
"""

import uuid
from dataclasses import dataclass

MAX_ATTEMPTS_BEFORE_TERMINAL = 5
_RETRY_BACKOFF_SECONDS = 30

# How long a claimed (GENERATING) row is considered actively owned before
# it becomes reclaimable by anyone. Comfortably longer than any real
# generation attempt (a pure in-process computation plus a handful of
# SQL statements) while still bounding the outage window from an
# unbounded stuck row to this fixed duration.
LEASE_DURATION_SECONDS = 120

_TERMINAL_STATUSES = frozenset({"COMPLETE", "LIMITED_EVIDENCE", "FAILED_TERMINAL"})


def new_claimant_token() -> str:
    """A privacy-safe, per-attempt opaque lease token — never a raw
    hostname, IP address, PID, or any user-identifying value. Callers
    generate one per claim attempt and must present the SAME token back
    to every mark_* call for that attempt."""
    return uuid.uuid4().hex


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
    claimed_by: str | None


def claim_next_attempt(conn, *, outbox_id: int, user_id: str, claimant: str) -> OutboxRow | None:
    """Atomically claims one outbox row for processing in a single UPDATE
    that handles all three claimable cases at once:

      A. status = PENDING
      B. status = FAILED_RETRYABLE and next_attempt_at has passed (or is
         unset) — retry backoff enforced by the WHERE clause itself.
      C. status = GENERATING and lease_expires_at has passed — a prior
         claimant's lease expired (it crashed or otherwise never marked
         the row terminal/retryable) and is now reclaimable by anyone.

    Also enforces MAX_ATTEMPTS_BEFORE_TERMINAL: if the attempt this call
    would make (attempt_count + 1) would exceed the limit, the SAME
    atomic statement settles the row FAILED_TERMINAL instead of claiming
    it — there is no separate, race-prone "check then act" step. In that
    case this function returns the (now-terminal) row so the caller can
    observe what happened, but the caller must check
    `result.status == "GENERATING"` before treating a non-None return as
    "I now own this row" — a FAILED_TERMINAL result means the row
    settled, not that anything should be generated.

    Returns None if the row doesn't exist, isn't owned by `user_id`, or
    is not currently in any claimable state (already actively
    GENERATING under a non-expired lease, or already COMPLETE/
    LIMITED_EVIDENCE/FAILED_TERMINAL) — the caller must treat None as
    "nothing to do right now," never as an error.
    """
    row = conn.execute(
        """WITH claimable AS (
               SELECT id, (attempt_count + 1 > %s) AS exceeds_limit
               FROM paper_trade_postmortem_outbox
               WHERE id = %s AND user_id = %s
                 AND (
                     status = 'PENDING'
                     OR (status = 'FAILED_RETRYABLE' AND (next_attempt_at IS NULL OR next_attempt_at <= now()))
                     OR (status = 'GENERATING' AND lease_expires_at IS NOT NULL AND lease_expires_at <= now())
                 )
           )
           UPDATE paper_trade_postmortem_outbox o
           SET status = CASE WHEN c.exceeds_limit THEN 'FAILED_TERMINAL' ELSE 'GENERATING' END,
               attempt_count = o.attempt_count + 1,
               last_attempt_at = now(),
               claimed_at = CASE WHEN c.exceeds_limit THEN o.claimed_at ELSE now() END,
               lease_expires_at = CASE WHEN c.exceeds_limit THEN NULL
                                        ELSE now() + make_interval(secs => %s) END,
               claimed_by = CASE WHEN c.exceeds_limit THEN NULL ELSE %s END,
               completed_at = CASE WHEN c.exceeds_limit THEN now() ELSE o.completed_at END,
               last_error_code = CASE WHEN c.exceeds_limit THEN 'MAX_ATTEMPTS_EXCEEDED' ELSE o.last_error_code END,
               last_error_summary = CASE WHEN c.exceeds_limit
                                          THEN 'exceeded max attempts before terminal'
                                          ELSE o.last_error_summary END
           FROM claimable c
           WHERE o.id = c.id
           RETURNING o.id, o.paper_trade_id, o.user_id, o.requested_report_schema_version,
                     o.requested_calculation_version, o.requested_rules_version, o.status,
                     o.attempt_count, o.source_request_id, o.claimed_by""",
        (MAX_ATTEMPTS_BEFORE_TERMINAL, outbox_id, user_id, LEASE_DURATION_SECONDS, claimant),
    ).fetchone()
    if row is None:
        return None
    return OutboxRow(*row)


def find_claimable_outbox_for_trade(conn, *, trade_id: int, user_id: str) -> OutboxRow | None:
    """Looks up the current outbox row for a trade (there is at most one
    per live version triple — see the unique index) without claiming it.
    Used by POST /generate to decide whether an outbox row already exists
    for this trade+version, or whether one must be created first (the
    historical-trade case)."""
    row = conn.execute(
        """SELECT id, paper_trade_id, user_id, requested_report_schema_version,
                  requested_calculation_version, requested_rules_version, status, attempt_count,
                  source_request_id, claimed_by
           FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND user_id = %s
           ORDER BY created_at DESC LIMIT 1""",
        (trade_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return OutboxRow(*row)


def mark_terminal(conn, *, outbox_id: int, status: str, claimed_by: str) -> bool:
    """status must be one of COMPLETE/LIMITED_EVIDENCE/FAILED_TERMINAL —
    the three statuses this outbox row can permanently settle into.
    Retryable failures use mark_retryable_failure instead.

    Requires the row to still be GENERATING under the SAME `claimed_by`
    token this caller was issued at claim time — if the lease has since
    expired and been reclaimed by someone else, this is a safe no-op:
    returns False rather than overwriting the new claimant's work. Every
    caller MUST check the return value; a stale worker silently assuming
    success would be exactly the "stale worker overwrites current
    worker" bug this lease design exists to prevent."""
    if status not in _TERMINAL_STATUSES:
        raise ValueError(f"mark_terminal requires a terminal status, got {status!r}")
    row = conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = %s, completed_at = now(), lease_expires_at = NULL, claimed_by = NULL
           WHERE id = %s AND status = 'GENERATING' AND claimed_by = %s
           RETURNING id""",
        (status, outbox_id, claimed_by),
    ).fetchone()
    return row is not None


def mark_retryable_failure(conn, *, outbox_id: int, error_code: str, error_summary: str, claimed_by: str) -> bool:
    """Never stores a raw exception message, secret, connection string,
    or report narrative — error_code is a stable machine-readable
    identifier (e.g. "GENERATION_ERROR"), error_summary is a short,
    pre-sanitized human description supplied by the caller, never an
    f-string of the original exception object. Same lease-ownership
    guard and boolean return contract as mark_terminal."""
    row = conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = 'FAILED_RETRYABLE', last_error_code = %s, last_error_summary = %s,
               next_attempt_at = now() + make_interval(secs => %s),
               lease_expires_at = NULL, claimed_by = NULL
           WHERE id = %s AND status = 'GENERATING' AND claimed_by = %s
           RETURNING id""",
        (error_code, error_summary, _RETRY_BACKOFF_SECONDS, outbox_id, claimed_by),
    ).fetchone()
    return row is not None


def mark_terminal_failure(conn, *, outbox_id: int, error_code: str, error_summary: str, claimed_by: str) -> bool:
    """Same lease-ownership guard and boolean return contract as
    mark_terminal — used for failures the caller already knows are not
    worth retrying (e.g. the trade row itself is missing)."""
    row = conn.execute(
        """UPDATE paper_trade_postmortem_outbox
           SET status = 'FAILED_TERMINAL', completed_at = now(), last_error_code = %s, last_error_summary = %s,
               lease_expires_at = NULL, claimed_by = NULL
           WHERE id = %s AND status = 'GENERATING' AND claimed_by = %s
           RETURNING id""",
        (error_code, error_summary, outbox_id, claimed_by),
    ).fetchone()
    return row is not None
