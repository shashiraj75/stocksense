"""
Buy idempotency — Trade Postmortem Engine, migration-verification hardening
gate, Part 7.

Problem this solves: a duplicate POST /buy (rapid double-click bypassing
the frontend's own in-flight guard, a network retry after a timeout, two
browser tabs, a client retrying after losing the response to a dropped
connection) previously created a second real trade with a second real cash
debit — the frontend-only protection proven in the prior hardening pass
was correctly labeled PARTIAL, not a fix. This module is the backend half:
a durable, persisted idempotency record keyed by
`(user_id, operation_type, idempotency_key)`, never by symbol/quantity/
timestamp matching (two genuine purchases of the same stock and quantity,
submitted with two different keys, must both succeed).

Architecture Decision Record — persistence design (Part 6's evaluation):
  Chosen: a dedicated `paper_trade_idempotency_key` table (option 1),
  not a unique column on `paper_trades` itself (option 2). Reasons:
    - The key must be reservable BEFORE it's known whether a trade will
      even be created (insufficient funds, market closed) — a column on
      `paper_trades` can't represent "reserved, no trade exists yet."
    - A replayed request must return the ORIGINAL response verbatim,
      which requires storing that response independently of whatever the
      trade row itself would compute if read back later.
    - Failed/abandoned attempts (crash before commit) must be visible and
      reclaimable without a phantom trade row to hang the state off of.
  Atomicity: reservation is a single `INSERT ... ON CONFLICT DO NOTHING`
  statement — Postgres guarantees exactly one concurrent writer wins that
  race for a given unique key, which is what actually makes "concurrent
  identical requests create one trade only" true; nothing in this module
  independently re-implements that guarantee, it relies on the database's
  own unique-index enforcement.
  Retention: no automatic TTL/cleanup job is implemented in this stage
  (would require a new scheduled worker — out of scope here, same
  boundary as prior stages). The only cleanup path is
  POST /reset?market=ALL, which also clears idempotency records — see
  paper_trading.py's reset_portfolio. Documented as a residual item, not
  silently omitted.

This module contains only pure decision logic and the fingerprint
calculation — no I/O. The router (api/routers/paper_trading.py) owns all
database access, exactly like every other module in this package.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

OPERATION_TYPE_PAPER_BUY = "PAPER_BUY"

# Trade Postmortem Engine, Sprint 2 correction phase (Stage 6) — a
# DISTINCT operation type, never reusing OPERATION_TYPE_PAPER_BUY. Same
# table (paper_trade_idempotency_key), same (user_id, operation_type,
# idempotency_key) unique-index contract, same decide_action/
# IdempotencyAction engine below — only the fingerprinted fields and the
# call site differ (paper_sell, not paper_buy). A Buy key and a Sell key
# with the same string value for the same user never collide, since the
# unique index and every lookup include operation_type.
OPERATION_TYPE_PAPER_SELL = "PAPER_SELL"

# A PENDING row older than this is treated as abandoned (the process that
# reserved it crashed before marking it COMPLETED or FAILED) rather than
# "still genuinely processing" — see `decide_action`. A synchronous DB
# transaction for one Buy should complete in well under a second; this is a
# deliberately generous margin, not a tuned production SLA.
STALE_PENDING_THRESHOLD = timedelta(seconds=30)

# Bounded, short synchronous wait for a genuinely concurrent in-flight
# request (not a stale one) to complete, so an ordinary rapid double-click
# gets the real completed result back instead of a spurious "still
# processing" error. Total worst-case added latency for the LOSING request
# of a race: POLL_ATTEMPTS * POLL_INTERVAL_SECONDS.
POLL_ATTEMPTS = 5
POLL_INTERVAL_SECONDS = 0.2

_MIN_KEY_LENGTH = 8
_MAX_KEY_LENGTH = 128
_ALLOWED_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


class IdempotencyRowStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IdempotencyAction(str, Enum):
    """What the router should do next, given the outcome of the
    reservation attempt and (if one existed) the prior row's state."""

    PROCEED_FRESH = "PROCEED_FRESH"          # no key supplied, or a fresh row was reserved
    PROCEED_RECLAIMED = "PROCEED_RECLAIMED"  # a FAILED or stale-PENDING row was reclaimed
    REPLAY_COMPLETED = "REPLAY_COMPLETED"    # return the stored response verbatim
    CONFLICT_FINGERPRINT_MISMATCH = "CONFLICT_FINGERPRINT_MISMATCH"
    STILL_IN_PROGRESS = "STILL_IN_PROGRESS"  # genuinely concurrent, not stale — poll exhausted


def validate_idempotency_key_format(key: str) -> None:
    """Raises ValueError with a client-safe message on any format problem.
    Deliberately permissive about content (a UUID is the expected shape,
    but this doesn't hard-require UUID syntax specifically) while still
    bounding length and character set — the two properties that actually
    matter for safe storage and indexing."""
    if not (_MIN_KEY_LENGTH <= len(key) <= _MAX_KEY_LENGTH):
        raise ValueError(f"idempotency_key must be between {_MIN_KEY_LENGTH} and {_MAX_KEY_LENGTH} characters")
    if not set(key) <= _ALLOWED_KEY_CHARS:
        raise ValueError("idempotency_key may only contain letters, digits, hyphens, and underscores")


def _normalize_float(value) -> str:
    if value is None:
        return "null"
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "invalid"
    return format(round(float(value), 6), ".6f")


def compute_request_fingerprint(
    *,
    market: str,
    symbol: str,
    quantity: int,
    price: float,
    stop_loss: float | None,
    target_price: float | None,
    trade_management_mode: str,
    evidence_source: str,
    entry_evidence_schema_version: str | None,
) -> str:
    """Deterministic SHA-256 hex digest of the financially material Buy
    fields, normalized before hashing (uppercased symbol/market, fixed
    6-decimal float formatting, explicit null sentinel) so equivalent
    requests always hash identically regardless of incidental formatting
    differences (e.g. 100 vs 100.0 vs 100.000001 rounding). Contains no
    secrets, no user ID, no timestamp — purely the trade's own economic
    terms, safe to store and to log at debug level if ever needed."""
    normalized = {
        "market": (market or "").upper(),
        "symbol": (symbol or "").upper(),
        "quantity": int(quantity) if isinstance(quantity, int) and not isinstance(quantity, bool) else "invalid",
        "price": _normalize_float(price),
        "stop_loss": _normalize_float(stop_loss),
        "target_price": _normalize_float(target_price),
        "trade_management_mode": trade_management_mode or "",
        "evidence_source": evidence_source or "",
        "entry_evidence_schema_version": entry_evidence_schema_version or "",
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_close_request_fingerprint(*, trade_id: int, exit_price: float, exit_reason: str | None) -> str:
    """Deterministic SHA-256 hex digest of the financially material Sell
    (close) fields — mirrors compute_request_fingerprint's own
    normalization discipline (fixed 6-decimal float formatting, explicit
    null sentinel), scoped to exactly the fields that determine what this
    close request financially does. `trade_id` is included so a Sell key
    reused (by mistake or intentionally) against a DIFFERENT trade is
    correctly treated as a fingerprint mismatch, never silently applied
    to the wrong trade."""
    normalized = {
        "trade_id": int(trade_id) if isinstance(trade_id, int) and not isinstance(trade_id, bool) else "invalid",
        "exit_price": _normalize_float(exit_price),
        "exit_reason": exit_reason or "",
    }
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExistingIdempotencyRow:
    """Typed shape of an existing `paper_trade_idempotency_key` row, as
    read by the router. Mirrors the table's columns relevant to the
    decision logic — not every column (e.g. `id` isn't needed here)."""

    status: str
    request_fingerprint: str
    response_body: dict | None
    created_at: datetime


def decide_action(
    existing: ExistingIdempotencyRow | None,
    new_fingerprint: str,
    *,
    now: datetime | None = None,
) -> IdempotencyAction:
    """Pure decision function — given what (if anything) already exists for
    this (user_id, operation_type, idempotency_key), what should the
    router do next. No I/O, no sleeping (the router owns the actual poll
    loop using POLL_ATTEMPTS/POLL_INTERVAL_SECONDS; this function is called
    once per poll attempt with a freshly re-read `existing`).
    `now` is injectable for deterministic testing; defaults to the real
    current time.
    """
    if existing is None:
        return IdempotencyAction.PROCEED_FRESH

    if existing.request_fingerprint != new_fingerprint:
        return IdempotencyAction.CONFLICT_FINGERPRINT_MISMATCH

    if existing.status == IdempotencyRowStatus.COMPLETED.value:
        return IdempotencyAction.REPLAY_COMPLETED

    if existing.status == IdempotencyRowStatus.FAILED.value:
        return IdempotencyAction.PROCEED_RECLAIMED

    # PENDING — genuinely in-flight, or abandoned by a crashed process.
    current_time = now if now is not None else datetime.now(timezone.utc)
    created_at = existing.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if current_time - created_at > STALE_PENDING_THRESHOLD:
        return IdempotencyAction.PROCEED_RECLAIMED

    return IdempotencyAction.STILL_IN_PROGRESS
