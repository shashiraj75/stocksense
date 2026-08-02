"""
US Daily Picks premarket finalizer — Phase 2 of the 3-phase US Daily Picks
upgrade (see .github/workflows/daily_picks_us_premarket.yml).

Problem this solves: the heavy US base Daily Picks run
(.github/workflows/daily_picks_us.yml, 06:00 UTC = 10:00 AM Dubai = 11:30 AM
IST) completes well before market open. This module provides a lightweight,
separate finalizer that re-checks the already-generated base picks at
approximately 6:00 AM America/New_York — the authoritative finalization
attempt for this feature — using whatever premarket-relevant signals this
codebase already has a safe, existing provider for. It never recomputes the
US universe, never re-runs PredictionEngine, and never invents data for a
signal this codebase has no provider for. See in_premarket_window() for the
exact 6:00-7:30 AM ET acceptance window and DST handling.

What is explicitly NOT done here, and why:
  - No new stock-universe scan, no re-scoring, no re-ranking — Phase 0-7 of
    generate_picks() (daily_picks.py) is the sole owner of that pipeline.
  - No confidence/scoring/ranking/stop-loss/target/regime mutation — this
    feature's safety rules and SES-001 risk-matching forbid weakening or
    silently changing the base pipeline's decision logic. This module only
    ever ADDS metadata fields to the already-persisted payload/pick dicts.
  - No backup-candidate substitution ("replace_from_backup" is defined as
    an allowed per-pick action label but never exercised) — generate_picks()
    does not persist runner-up candidates beyond the top-6-per-horizon
    selection, so there is nothing to replace from. Inventing a backup list
    here would violate "do not invent missing data."
  - No fresh scrape of a data source this codebase doesn't already have a
    provider for (premarket volume, incremental news-since-timestamp,
    sector-index movement, an earnings calendar, bid/ask spread) — each is
    reported as an explicit missing input, never fabricated.

Data inputs actually available (existing providers only):
  - price_gap_pct: the latest available quote from
    services.market_data.MarketDataService (already used by
    GET /api/stocks/quote) compared against the pick's recorded
    generation-time price. Labelled honestly as "latest available quote",
    not verified true premarket-session tick data — yfinance's fast_info
    does not reliably distinguish a premarket print from the prior close.
  - index_proxy_direction: S&P 500 / NASDAQ % change from the existing
    services.global_context.get_global_context() macro snapshot.

Known limitation: holiday detection reuses services.market_hours's US
fixed-holiday calendar (Good Friday + the standard NYSE closures), which is
exchange-calendar-aware, not merely Mon-Fri — but it does not cover ad hoc
one-off market closures (e.g. a national day of mourning) since those have
no closed-form date and are not in that calendar either.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# 1.1.0 — Daily Picks Scheduler Remediation Phase 1A: fail-closed base-job
# provenance verification. A deliberate, non-additive-only bump: a base
# payload that previously finalized successfully (any US payload with
# picks, regardless of which day or job produced it) can now be rejected
# or skipped if it lacks verified same-day, same-job provenance. See
# validate_base_for_finalization() and finalize_premarket() below.
PREMARKET_FINALIZER_VERSION = "1.1.0"
PREMARKET_TIMEZONE = "America/New_York"
_ET = ZoneInfo(PREMARKET_TIMEZONE)

# ── Fail-closed base-validation outcome codes ──────────────────────────────
# Every non-"finalized" outcome below is asserted to make zero provider
# calls and zero persistence calls (see tests). "already_finalized" is
# produced by finalize_premarket() itself (exact source_job_id/
# base_generated_at idempotency), not by validate_base_for_finalization().
_OUTCOME_STATUS_MAP = {
    "already_finalized":              "skipped",
    "skipped_no_base":                "skipped",
    "skipped_stale_base":             "skipped",
    "skipped_base_running":           "skipped",
    "skipped_base_failed":            "skipped",
    "skipped_base_state_unavailable": "skipped",
    "rejected_malformed_base":        "failed",
    "durable_persistence_failed":     "failed",
}

# daily_picks_jobs.status values that mean "not yet decided" vs "decided
# but not usable." A status outside both sets (including any future,
# not-yet-existing value) fails closed via rejected_malformed_base rather
# than being guessed at — see validate_base_for_finalization().
_JOB_STATUS_ACTIVE = frozenset({"queued", "running"})
_JOB_STATUS_TERMINAL_FAILURE = frozenset({"failed", "interrupted", "expired"})


def _validate_payload_preflight(
    payload: dict | None,
    now_et: datetime,
) -> tuple[str | None, dict]:
    """
    Stage 1 — payload-only structural and freshness validation (checks
    A-F). No I/O, no job lookup, no mutation of `payload`. This must pass
    BEFORE exact-base idempotency is considered and before Stage 2
    (originating-job verification) runs — see finalize_premarket()'s
    required ordering. A stale, malformed, or wrong-market payload is
    rejected/skipped here regardless of what finalization markers it
    carries; carrying a self-consistent-looking marker pair is never a
    substitute for passing these checks.

    Returns (outcome, detail):
      outcome is None  → payload has actual picks, market == "US",
                          generated_at is a well-formed tz-aware timestamp
                          belonging to today (America/New_York), and
                          source_job_id is a non-empty string.
      outcome is a str → one of the precise machine-safe reason codes in
                          _OUTCOME_STATUS_MAP; caller must stop here.
    """
    detail: dict = {}

    # A. Payload exists and has actual (non-empty) picks.
    if not payload:
        return "skipped_no_base", detail
    picks = payload.get("picks")
    has_picks = isinstance(picks, dict) and any(
        isinstance(v, list) and len(v) > 0 for v in picks.values()
    )
    if not has_picks:
        return "skipped_no_base", detail

    # B. payload.market is exactly "US".
    if payload.get("market") != "US":
        detail["malformed_reason"] = "payload_market_not_us"
        return "rejected_malformed_base", detail

    # C. payload.generated_at exists.
    generated_at_raw = payload.get("generated_at")
    if not generated_at_raw:
        detail["malformed_reason"] = "missing_generated_at"
        return "rejected_malformed_base", detail

    # D. generated_at parses as a timezone-aware ISO timestamp. Trailing
    # "Z" is supported explicitly (matches picks_generated_today's own
    # convention); a naive timestamp is rejected outright — never guessed.
    try:
        generated_at = datetime.fromisoformat(str(generated_at_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        detail["malformed_reason"] = "unparseable_generated_at"
        return "rejected_malformed_base", detail
    if generated_at.tzinfo is None:
        detail["malformed_reason"] = "naive_generated_at"
        return "rejected_malformed_base", detail

    # E. generated_at, converted to America/New_York, belongs to
    # now_et.date() — the core fresh-base check this phase exists to add.
    generated_at_et = generated_at.astimezone(_ET)
    if generated_at_et.date() != now_et.date():
        detail["base_generated_at"] = generated_at_raw
        detail["base_generated_date_et"] = generated_at_et.date().isoformat()
        return "skipped_stale_base", detail

    # F. source_job_id exists and is non-empty.
    source_job_id = payload.get("source_job_id")
    if not source_job_id:
        detail["malformed_reason"] = "missing_source_job_id"
        return "rejected_malformed_base", detail
    detail["source_job_id"] = source_job_id

    return None, detail


def _check_existing_finalization(
    payload: dict,
    generated_at: datetime,
    source_job_id: str,
) -> tuple[str, dict]:
    """
    Validates the (premarket_finalized_for_job_id, premarket_finalized_for_base)
    marker pair against the CURRENT base. Only called after
    _validate_payload_preflight (Stage 1) has already confirmed `payload`
    is structurally sound and fresh (today, ET) — `generated_at` and
    `source_job_id` here are therefore already known-good.

    A freshly generated replacement base (a genuine new generate_picks()
    run) is constructed from scratch and normally carries NEITHER marker
    at all — that is the expected shape of "never finalized yet," handled
    below as status "absent". Markers that ARE present but don't describe
    THIS exact payload are never assumed to be a harmless leftover from
    some other legitimate finalization; this codebase has no path that
    intentionally carries old markers forward onto a new base, so their
    presence-but-mismatch is treated as unverifiable/contradictory
    provenance and fails closed, never silently re-validated as if it
    were a "no markers" fresh base.

    Returns (status, detail):
      "absent"    → neither marker present; this base has never been
                    finalized. Caller proceeds to Stage 2 (job lookup).
      "match"     → both markers present, structurally valid, and refer
                    to exactly this job_id and this generated_at instant.
                    Caller returns already_finalized with zero further
                    job lookup, provider, or persistence calls.
      "malformed" → exactly one marker present, a marker fails to parse,
                    or both are present but refer to a different job_id
                    or a different base instant than this payload.
                    detail["malformed_reason"] identifies which.
    """
    marker_job_id = payload.get("premarket_finalized_for_job_id")
    marker_base_raw = payload.get("premarket_finalized_for_base")
    has_job_marker = marker_job_id is not None
    has_base_marker = marker_base_raw is not None

    if not has_job_marker and not has_base_marker:
        return "absent", {}

    if has_job_marker != has_base_marker:
        return "malformed", {"malformed_reason": "partial_finalization_marker"}

    if not isinstance(marker_job_id, str) or not marker_job_id:
        return "malformed", {"malformed_reason": "invalid_finalization_marker_job_id"}

    # Compare by normalized UTC instant, never by raw string equality — a
    # semantically identical timestamp written with a "Z" suffix vs
    # "+00:00" must still compare equal.
    try:
        marker_base = datetime.fromisoformat(str(marker_base_raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "malformed", {"malformed_reason": "unparseable_finalization_marker_base"}
    if marker_base.tzinfo is None:
        return "malformed", {"malformed_reason": "naive_finalization_marker_base"}

    if marker_job_id == source_job_id and marker_base.astimezone(timezone.utc) == generated_at.astimezone(timezone.utc):
        return "match", {}

    return "malformed", {"malformed_reason": "finalization_marker_mismatch"}


def _validate_source_job_state(
    job_row: dict | None,
    job_lookup_failed: bool,
) -> tuple[str | None, dict]:
    """
    Stage 2 — originating-job verification (checks G-K). Only reached for
    a base that Stage 1 already confirmed is structurally sound and fresh,
    and that is not already exact-base-idempotent-finalized.

    job_row: the result of postgres_store.get_daily_picks_job_by_id(
        payload["source_job_id"]) — a dict if the row was found, None if
        genuinely not found (only meaningful when job_lookup_failed=False).
    job_lookup_failed: True iff the caller could not perform the lookup at
        all (no durable job state available, or the lookup itself raised) —
        distinguishes "this job_id does not exist" from "could not check."
    """
    detail: dict = {}

    # G onward require a durable job-state lookup. A lookup that could not
    # be performed at all fails closed distinctly from "not found."
    if job_lookup_failed:
        return "skipped_base_state_unavailable", detail

    # G. The exact source job exists — never inferred by "latest job for
    # US," "closest timestamp," "same calendar date," or any other proxy.
    if job_row is None:
        detail["malformed_reason"] = "source_job_not_found"
        return "rejected_malformed_base", detail

    # H. Source job market is "US" — the payload's own market claim (B)
    # must agree with the job that actually produced it.
    if job_row.get("market") != "US":
        detail["malformed_reason"] = "source_job_market_mismatch"
        detail["source_job_status"] = job_row.get("status")
        return "rejected_malformed_base", detail

    job_status = job_row.get("status")
    detail["source_job_status"] = job_status

    # I. Source job status is "completed" — branch by status otherwise.
    if job_status in _JOB_STATUS_ACTIVE:
        return "skipped_base_running", detail
    if job_status in _JOB_STATUS_TERMINAL_FAILURE:
        return "skipped_base_failed", detail
    if job_status != "completed":
        # Any other/unrecognized status (including a future value this
        # module doesn't yet know about) — fail closed rather than guess.
        detail["malformed_reason"] = "source_job_status_unrecognized"
        return "rejected_malformed_base", detail

    # J. completed_at is non-null.
    if not job_row.get("completed_at"):
        detail["malformed_reason"] = "completed_job_missing_completed_at"
        return "rejected_malformed_base", detail

    # K. persisted_picks_timestamp is non-null.
    if not job_row.get("persisted_picks_timestamp"):
        detail["malformed_reason"] = "completed_job_missing_persisted_picks_timestamp"
        return "rejected_malformed_base", detail

    return None, detail


def validate_base_for_finalization(
    payload: dict | None,
    now_et: datetime,
    job_row: dict | None,
    job_lookup_failed: bool,
) -> tuple[str | None, dict]:
    """
    Pure, directly-testable fail-closed validation of a base Daily Picks
    payload before premarket finalization: Stage 1 payload-only checks
    (A-F, see _validate_payload_preflight) composed with Stage 2
    originating-job checks (G-K, see _validate_source_job_state). No I/O
    beyond what the caller already did to produce job_row, no mutation of
    `payload`, no provider calls.

    job_row / job_lookup_failed: see _validate_source_job_state.

    Returns (outcome, detail):
      outcome is None  → the base is fully valid and fresh (checks A-K all
                          passed) — caller proceeds to real finalization.
      outcome is a str → one of the precise machine-safe reason codes in
                          _OUTCOME_STATUS_MAP; caller must stop here.
    detail is a dict of diagnostic fields to surface in the response —
    never used to mutate `payload`.
    """
    outcome, detail = _validate_payload_preflight(payload, now_et)
    if outcome is not None:
        return outcome, detail

    outcome2, detail2 = _validate_source_job_state(job_row, job_lookup_failed)
    detail = {**detail, **detail2}
    if outcome2 is not None:
        return outcome2, detail
    return None, detail


# Allowed per-pick finalizer actions. See module docstring for which are
# actually exercised today ("upgrade"/"downgrade"/"replace_from_backup" are
# reserved, not yet exercised — see the docstring for why).
ALLOWED_PICK_ACTIONS = frozenset({
    "keep", "upgrade", "downgrade", "replace_from_backup",
    "mark_premarket_risk", "skip_due_to_data_unavailable",
})

# Premarket price-gap magnitude, in percent, above which a pick is flagged
# mark_premarket_risk rather than silently kept. Additive-only signal —
# never changes the pick's own signal/confidence/target/stop-loss fields.
PRICE_GAP_RISK_THRESHOLD_PCT = 3.0

# Inputs this codebase has no existing safe provider for today. Always
# reported as missing — never fabricated. See module docstring.
_ALWAYS_MISSING_INPUTS = (
    "premarket_volume", "fresh_news_since_generation", "sector_movement",
    "earnings_event_risk", "abnormal_volatility_spread_risk",
)


def in_premarket_window(now_et: datetime) -> bool:
    """6:00-7:30 AM America/New_York, Mon-Fri, excluding US market holidays.

    2026-07-15 change: retargeted from 7:30-9:00 AM (see prior docstring
    below, and CHANGELOG.md / Product-Integrity docs for that history, left
    unchanged as historical evidence) to make 6:00 AM ET the authoritative
    finalization attempt, while keeping the same ~90-minute width that
    absorbs GitHub Actions' own scheduling delay. 7:30 AM ET is chosen as
    the new upper bound to stay well before the 9:30 AM open without
    blurring into regular-session data, mirroring the old design's rationale
    at its new target.

    Prior (2026-07-13) rationale, preserved for context: widened from the
    original 8:00-8:30 (30 min) after a real missed run — the triggering
    GitHub Actions cron (daily_picks_us_premarket.yml) didn't fire at all
    before the old, narrower window closed (confirmed live: 0 recorded
    workflow runs, premarket_finalized_at still null well past the old
    window's close). GitHub Actions scheduled triggers are best-effort and
    can run late by more than 30 minutes; 90 minutes absorbs that class of
    delay (this repo has seen a multi-hour delay on a different Daily Picks
    workflow — see Product-Integrity-001C — which this width still would not
    cover, a known, accepted residual risk, not a claim of a hard guarantee).

    Reuses the existing exchange-calendar holiday utility
    (services.market_hours._us_fixed_holidays) rather than a Mon-Fri-only
    guard — see the module docstring's Known Limitation note for its scope."""
    from services.market_hours import _us_fixed_holidays
    if now_et.weekday() >= 5:
        return False
    if now_et.date() in _us_fixed_holidays(now_et.year):
        return False
    start = now_et.replace(hour=6, minute=0, second=0, microsecond=0)
    end = now_et.replace(hour=7, minute=30, second=0, microsecond=0)
    return start <= now_et <= end


async def _price_gap_for_pick(pick: dict) -> None:
    """Mutates `pick` in place with premarket_* fields. Never raises —
    any failure degrades to skip_due_to_data_unavailable, isolated per pick
    so one symbol's failure can't affect the others (SES-002 §6)."""
    symbol = pick.get("symbol")
    base_price = pick.get("generation_reference_price") or pick.get("price")
    checked_at = datetime.now(timezone.utc).isoformat()
    pick["premarket_checked_at"] = checked_at

    if not symbol or not base_price:
        pick["premarket_action"] = "skip_due_to_data_unavailable"
        pick["premarket_reason"] = "no generation-time price on record to compare against"
        pick["premarket_data_available"] = False
        pick["premarket_warning"] = "missing_base_price"
        return

    try:
        from services.market_data import MarketDataService
        quote = await MarketDataService().get_quote(symbol, "US")
        current = quote.get("price") if quote else None
    except Exception as e:
        log.warning(f"[premarket_finalizer] quote fetch failed for {symbol}: {e}")
        current = None

    if not current:
        pick["premarket_action"] = "skip_due_to_data_unavailable"
        pick["premarket_reason"] = "premarket quote unavailable from existing quote provider"
        pick["premarket_data_available"] = False
        pick["premarket_warning"] = "quote_unavailable"
        return

    gap_pct = round((float(current) - float(base_price)) / float(base_price) * 100, 2)
    pick["premarket_data_available"] = True
    if abs(gap_pct) >= PRICE_GAP_RISK_THRESHOLD_PCT:
        pick["premarket_action"] = "mark_premarket_risk"
        pick["premarket_reason"] = (
            f"latest available quote is {gap_pct:+.2f}% vs generation-time price "
            f"(>= {PRICE_GAP_RISK_THRESHOLD_PCT}% threshold)"
        )
        pick["premarket_warning"] = "premarket_gap_risk"
    else:
        pick["premarket_action"] = "keep"
        pick["premarket_reason"] = (
            f"latest available quote is {gap_pct:+.2f}% vs generation-time price "
            f"(within {PRICE_GAP_RISK_THRESHOLD_PCT}% threshold)"
        )


async def _gather_index_proxy() -> dict | None:
    """Returns {"sp500_change_pct", "nasdaq_change_pct"} or None on failure.
    Never invents a value — None means genuinely unavailable this run."""
    try:
        from services.global_context import get_global_context
        loop = asyncio.get_running_loop()
        ctx = await loop.run_in_executor(None, get_global_context)
        changes = ctx.get("changes", {}) if isinstance(ctx, dict) else {}
        sp500, nasdaq = changes.get("sp500"), changes.get("nasdaq")
        if sp500 is None and nasdaq is None:
            return None
        return {"sp500_change_pct": sp500, "nasdaq_change_pct": nasdaq}
    except Exception as e:
        log.warning(f"[premarket_finalizer] index proxy fetch failed: {e}")
        return None


async def finalize_premarket(market: str, now: datetime | None = None) -> dict:
    """
    Re-check the existing US base Daily Picks with whatever premarket
    signals this codebase already has a safe provider for. Never
    recomputes the universe, never mutates scoring/ranking/confidence/
    target/stop-loss, never invents missing data. See module docstring.

    Daily Picks Scheduler Remediation Phase 1A: the base payload is now
    fail-closed validated (validate_base_for_finalization) BEFORE any
    provider call or persistence — a stale, malformed, or unverifiable
    base payload is rejected/skipped with zero side effects, never
    silently finalized as if it were today's fresh, verified base.

    `now` is injectable (defaults to real UTC now) so tests can exercise
    the DST window guard deterministically. All finalization metadata
    (window-checked timestamp, finalized-at timestamp) is derived from
    this SAME injected value, converted to UTC — never a second, separate
    call to datetime.now(timezone.utc), so validation and persisted
    metadata can never silently disagree about "now."
    """
    if market != "US":
        return {"market": market, "status": "skipped", "reason": "unsupported_market"}

    effective_now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_et = effective_now_utc.astimezone(_ET)
    if not in_premarket_window(now_et):
        return {"market": "US", "status": "skipped", "reason": "outside_premarket_finalizer_window"}

    import services.daily_picks as _dp
    payload = _dp.get_cached_picks("US")

    # ── Stage 1: payload-only structural and freshness validation (A-F) ──
    # Must run, and must pass, BEFORE exact-base idempotency is even
    # considered — see _check_existing_finalization's docstring and the
    # Scheduler Remediation Phase 1A post-review finding this ordering
    # fixes. A stale, malformed, or wrong-market payload is rejected here
    # regardless of what finalization markers it happens to carry.
    outcome, detail = _validate_payload_preflight(payload, now_et)
    if outcome is not None:
        # US Daily Picks generation-reliability incident (2026-07-22, Phase
        # 6): a missing or stale base used to make this finalizer silently
        # no-op with a "skipped" status the UI badge showed as an
        # indefinite "Premarket Review Pending" — with no attempt to
        # recover. This finalizer already runs on a real schedule well
        # after the base's expected completion time, so it doubles as the
        # watchdog's check point: on exactly these two outcomes (base
        # genuinely absent, or present but not from today), it now
        # initiates a bounded, non-overlapping, governed recovery instead
        # of only reporting the gap. "rejected_malformed_base" is
        # deliberately excluded — that's a data-integrity problem a blind
        # retry could mask, not a missing-run problem a retry fixes.
        recovery = None
        if outcome in ("skipped_no_base", "skipped_stale_base"):
            try:
                import services.daily_picks as _dp
                recovery = _dp.attempt_governed_recovery("US", reason=outcome)
            except Exception as e:
                log.warning(f"[premarket_finalizer] governed recovery attempt failed: {e}")
        return {
            "market": "US",
            "status": _OUTCOME_STATUS_MAP[outcome],
            "reason": outcome,
            "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
            "recovery": recovery,
            **detail,
        }

    source_job_id = payload["source_job_id"]
    generated_at = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))

    # ── Exact-base idempotency — checked only AFTER Stage 1 above proves
    # this base is structurally sound and belongs to today (ET). This
    # shortcut exists for the second of the two fixed-UTC dual-cron
    # candidates (daily_picks_us_premarket.yml fires once for EDT, once
    # for EST) hitting the SAME already-finalized base same-day — it must
    # never be used to mask a stale or malformed base, which Stage 1 has
    # already ruled out by this point. Keyed on (source_job_id,
    # base_generated_at instant), never on calendar date alone — a
    # same-day base RE-run under a NEW source_job_id normally carries
    # neither marker at all (see _check_existing_finalization) and is
    # correctly treated as a fresh opportunity to finalize. Zero job
    # lookup, zero provider calls, zero persistence calls, zero payload
    # mutation on this path.
    marker_status, marker_detail = _check_existing_finalization(payload, generated_at, source_job_id)
    if marker_status == "match":
        return {
            "market": "US",
            "status": "skipped",
            "reason": "already_finalized",
            "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
            "premarket_finalized_at": payload.get("premarket_finalized_at"),
            "source_job_id": source_job_id,
            "base_generated_at": payload.get("generated_at"),
        }
    if marker_status == "malformed":
        return {
            "market": "US",
            "status": "failed",
            "reason": "rejected_malformed_base",
            "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
            "source_job_id": source_job_id,
            "base_generated_at": payload.get("generated_at"),
            **marker_detail,
        }

    # ── Stage 2: originating-job verification (G-K) — no provider calls,
    # no persistence yet ──
    job_row = None
    job_lookup_failed = False
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import get_daily_picks_job_by_id
            job_row = get_daily_picks_job_by_id(source_job_id)
        except Exception as e:
            log.warning(f"[premarket_finalizer] base-job lookup failed: {e}")
            job_lookup_failed = True
    else:
        # No durable job state available at all — provenance cannot be
        # verified. Production code must not bypass verification merely
        # because USE_POSTGRES is unexpectedly absent; fail closed.
        job_lookup_failed = True

    outcome, detail = _validate_source_job_state(job_row, job_lookup_failed)

    if outcome is not None:
        return {
            "market": "US",
            "status": _OUTCOME_STATUS_MAP[outcome],
            "reason": outcome,
            "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
            "source_job_id": source_job_id,
            **detail,
        }

    # Base is fully valid and fresh: payload has picks, market == "US",
    # generated_at is a tz-aware timestamp belonging to today (ET), and
    # source_job_id resolves to an exact, completed, durably-persisted US
    # job. base_universe_degraded is read directly off the base payload —
    # a degraded-but-genuinely-fresh-and-completed base is never treated
    # as equivalent to a stale or malformed one (see module policy below).
    base_generated_at = payload["generated_at"]
    base_universe_degraded = bool(payload.get("universe_degraded"))

    # ── Provider work — only ever reached after full validation passes ──────
    checked_at = effective_now_utc.isoformat()
    all_picks = [
        pick for items in payload.get("picks", {}).values() for pick in items
    ]

    index_proxy_task = asyncio.ensure_future(_gather_index_proxy())
    if all_picks:
        await asyncio.gather(*(_price_gap_for_pick(p) for p in all_picks))
    index_proxy = await index_proxy_task

    any_price_gap_succeeded = any(p.get("premarket_data_available") for p in all_picks)
    missing_inputs = list(_ALWAYS_MISSING_INPUTS)
    if index_proxy is None:
        missing_inputs.append("index_proxy_direction")
    if all_picks and not any_price_gap_succeeded:
        missing_inputs.append("price_gap_pct")

    data_available = any_price_gap_succeeded or index_proxy is not None

    # Additive metadata only — every existing field on `payload` (generated_at,
    # market, source_job_id, picks, alpha_engine, regime, screener_raw_count,
    # etc.) is preserved untouched; per-pick base fields (symbol, price,
    # target, stop_loss, confidence, signal, reasoning, factor_zscores,
    # ranking, ...) are likewise preserved — only premarket_*/base_* keys
    # are added. Shallow copy: `picks` is the SAME nested dict/lists the
    # loop above already mutated in place (unchanged from prior behavior).
    finalized_payload = dict(payload)
    finalized_payload["base_generated_at"] = base_generated_at
    finalized_payload["source_job_id"] = source_job_id
    finalized_payload["premarket_finalized_at"] = effective_now_utc.isoformat()
    finalized_payload["premarket_finalized_for_base"] = base_generated_at
    finalized_payload["premarket_finalized_for_job_id"] = source_job_id
    # premarket_status is preserved at its pre-existing value — a real
    # frontend consumer (frontend/src/app/picks/page.tsx's
    # PREMARKET_STATUS_LABEL/PREMARKET_STATUS_CLASS maps) keys off this
    # exact string; changing it would silently fall back to a generic
    # "Premarket Pending" display for a genuinely-completed finalization.
    # premarket_reason carries the new precise machine-safe code
    # additively, without touching the field any existing consumer reads.
    finalized_payload["premarket_status"] = "completed_with_limited_premarket_data"
    finalized_payload["premarket_reason"] = "finalized"
    finalized_payload["premarket_finalizer_version"] = PREMARKET_FINALIZER_VERSION
    finalized_payload["premarket_data_available"] = data_available
    finalized_payload["premarket_missing_inputs"] = sorted(set(missing_inputs))
    finalized_payload["premarket_window_checked"] = checked_at
    finalized_payload["premarket_timezone"] = PREMARKET_TIMEZONE
    finalized_payload["base_universe_degraded"] = base_universe_degraded
    if base_universe_degraded:
        # Explicit, machine-safe warning — a degraded-but-valid base is
        # never silently represented as healthy, and never treated as
        # equivalent to stale/malformed data (it still finalizes). Ranking,
        # picks, confidence, signal, target, and stop-loss are unaffected —
        # this is metadata only, mirroring universe_degraded's own existing
        # base-payload contract.
        finalized_payload["premarket_warnings"] = ["degraded_base_universe"]
    if index_proxy is not None:
        finalized_payload["premarket_index_proxy"] = index_proxy

    # ── Truthful persistence: Postgres success gates everything else ────────
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import save_picks_to_db
            saved = save_picks_to_db(finalized_payload, market="US")
        except Exception as e:
            log.warning(f"[premarket_finalizer] Postgres save raised: {e}")
            saved = False
        if not saved:
            # Durable persistence is the source of truth this system relies
            # on (survives Railway redeploys). A finalization that could not
            # be durably persisted must never be reported as completed, and
            # the disk cache must never be written first to make a failed
            # finalization look successful.
            return {
                "market": "US",
                "status": "failed",
                "reason": "durable_persistence_failed",
                "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
                "source_job_id": source_job_id,
                "base_generated_at": base_generated_at,
            }
        # Postgres succeeded — local disk copy is now best-effort only. A
        # disk-write failure here must never downgrade an already-durable
        # Postgres success back to a reported failure.
        try:
            import json as _json
            with open(_dp._cache_file("US"), "w") as f:
                _json.dump(finalized_payload, f)
        except Exception as e:
            log.warning(f"[premarket_finalizer] disk cache write failed after durable success: {e}")
    else:
        # Non-Postgres/local/test mode: with USE_POSTGRES != "1", provenance
        # could never have been verified above (job_lookup_failed is forced
        # True whenever a source_job_id is present), so this branch is
        # unreachable for any payload carrying real provenance — it exists
        # only so a direct, non-production call with no durable job state
        # has an explicit, testable local behavior rather than an implicit
        # "silently returns None" instead of a decisive persistence path.
        try:
            import json as _json
            with open(_dp._cache_file("US"), "w") as f:
                _json.dump(finalized_payload, f)
        except Exception as e:
            log.warning(f"[premarket_finalizer] disk cache write failed (non-Postgres mode): {e}")

    result = {
        "market": "US",
        "status": "completed",
        "reason": "finalized",
        "premarket_finalizer_version": PREMARKET_FINALIZER_VERSION,
        "premarket_data_available": data_available,
        "premarket_missing_inputs": finalized_payload["premarket_missing_inputs"],
        "source_job_id": source_job_id,
        "base_generated_at": base_generated_at,
        "base_universe_degraded": base_universe_degraded,
    }
    if base_universe_degraded:
        result["premarket_warnings"] = finalized_payload["premarket_warnings"]
    return result
