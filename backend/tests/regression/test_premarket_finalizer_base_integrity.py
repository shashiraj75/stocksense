"""
Daily Picks Scheduler Reliability Remediation — Phase 1A:
US Premarket Fresh-Base Integrity and Durable Base-Job Provenance.

Confirmed defect this phase closes: services/premarket_finalizer.py could
add today's premarket metadata to a prior-day (or otherwise unverified)
base payload, because it never checked that generated_at belonged to the
current America/New_York market date, never verified which Daily Picks job
produced the payload, and never confirmed that job completed successfully.

This file covers:
  1. validate_base_for_finalization() as a pure function — every check
     A-K, in isolation, with no mocking (it performs no I/O).
  2. finalize_premarket() integration behavior — the full fail-closed
     flow, exact-base idempotency, the degraded-universe policy, truthful
     Postgres-gated persistence, and timestamp consistency.

All tests are deterministic and fully mocked — no real DB, no external
providers, no network (SES-003).
"""
import copy
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────
# Part 1 — validate_base_for_finalization: pure function, no mocking needed
# ─────────────────────────────────────────────────────────────────────────

_NOW_ET = datetime(2026, 7, 6, 8, 15, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))


def _valid_payload(**overrides):
    payload = {
        "market": "US",
        "generated_at": "2026-07-06T04:10:00+00:00",  # same ET calendar date as _NOW_ET
        "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []},
        "source_job_id": "job-us-1",
    }
    payload.update(overrides)
    return payload


def _valid_job_row(**overrides):
    row = {
        "job_id": "job-us-1",
        "market": "US",
        "status": "completed",
        "started_at": "2026-07-06T03:00:00+00:00",
        "completed_at": "2026-07-06T04:00:00+00:00",
        "persisted_picks_timestamp": "2026-07-06T04:00:01+00:00",
        "universe_degraded": False,
        "last_error": None,
    }
    row.update(overrides)
    return row


@pytest.mark.regression
class TestValidateBaseForFinalization:
    def _validate(self, payload, job_row=None, job_lookup_failed=False):
        from services.premarket_finalizer import validate_base_for_finalization
        return validate_base_for_finalization(payload, _NOW_ET, job_row, job_lookup_failed)

    # A — payload existence / actual picks
    def test_no_base_payload(self):
        outcome, _ = self._validate(None)
        assert outcome == "skipped_no_base"

    def test_empty_picks_payload(self):
        outcome, _ = self._validate(_valid_payload(picks={"short": [], "medium": [], "long": []}))
        assert outcome == "skipped_no_base"

    def test_picks_key_missing_entirely(self):
        payload = _valid_payload()
        del payload["picks"]
        outcome, _ = self._validate(payload)
        assert outcome == "skipped_no_base"

    # B — wrong payload market
    def test_wrong_payload_market(self):
        outcome, detail = self._validate(_valid_payload(market="IN"))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "payload_market_not_us"

    # C — missing generated_at
    def test_missing_generated_at(self):
        payload = _valid_payload()
        del payload["generated_at"]
        outcome, detail = self._validate(payload)
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "missing_generated_at"

    # D — malformed / naive generated_at
    def test_malformed_generated_at(self):
        outcome, detail = self._validate(_valid_payload(generated_at="not-a-timestamp"))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "unparseable_generated_at"

    def test_naive_generated_at(self):
        outcome, detail = self._validate(_valid_payload(generated_at="2026-07-06T04:10:00"))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "naive_generated_at"

    # E — stale base / trailing-Z support
    def test_stale_prior_et_day_base(self):
        outcome, detail = self._validate(_valid_payload(generated_at="2026-07-05T04:10:00+00:00"))
        assert outcome == "skipped_stale_base"
        assert detail["base_generated_date_et"] == "2026-07-05"

    def test_trailing_z_generated_at_is_supported(self):
        """A same-day base using 'Z' suffix instead of '+00:00' must validate cleanly."""
        outcome, _ = self._validate(_valid_payload(generated_at="2026-07-06T04:10:00Z"),
                                     job_row=_valid_job_row())
        assert outcome is None

    def test_utc_timestamp_that_converts_to_prior_et_date(self):
        """
        02:00 UTC on 2026-07-06 is 22:00 ET on 2026-07-05 (EDT, UTC-4) — the
        UTC calendar date matches now_et's UTC-side date, but the ET-
        converted date does not. This proves the check uses the ET
        conversion, never the raw UTC date.
        """
        outcome, detail = self._validate(_valid_payload(generated_at="2026-07-06T02:00:00+00:00"))
        assert outcome == "skipped_stale_base"
        assert detail["base_generated_date_et"] == "2026-07-05"

    # F — missing source_job_id
    def test_missing_source_job_id(self):
        payload = _valid_payload()
        payload["source_job_id"] = None
        outcome, detail = self._validate(payload)
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "missing_source_job_id"

    def test_empty_string_source_job_id(self):
        outcome, detail = self._validate(_valid_payload(source_job_id=""))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "missing_source_job_id"

    # job lookup / database error — distinct from "not found"
    def test_job_lookup_database_error(self):
        outcome, _ = self._validate(_valid_payload(), job_row=None, job_lookup_failed=True)
        assert outcome == "skipped_base_state_unavailable"

    # G — source job not found (lookup succeeded, no row)
    def test_source_job_not_found(self):
        outcome, detail = self._validate(_valid_payload(), job_row=None, job_lookup_failed=False)
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "source_job_not_found"

    # H — source job belongs to a different market
    def test_source_job_belongs_to_in(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(market="IN"))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "source_job_market_mismatch"

    # I — source job status branches
    def test_source_job_queued(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="queued"))
        assert outcome == "skipped_base_running"
        assert detail["source_job_status"] == "queued"

    def test_source_job_running(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="running"))
        assert outcome == "skipped_base_running"
        assert detail["source_job_status"] == "running"

    def test_source_job_failed(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="failed"))
        assert outcome == "skipped_base_failed"
        assert detail["source_job_status"] == "failed"

    def test_source_job_interrupted(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="interrupted"))
        assert outcome == "skipped_base_failed"
        assert detail["source_job_status"] == "interrupted"

    def test_source_job_expired_status(self):
        """'expired' does not exist in the DB CHECK constraint yet (Scheduler
        Remediation Phase 3, not this phase) — recognized defensively now so
        this module never needs to change again when it's introduced."""
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="expired"))
        assert outcome == "skipped_base_failed"
        assert detail["source_job_status"] == "expired"

    def test_source_job_unrecognized_status_fails_closed(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(status="bogus"))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "source_job_status_unrecognized"

    # J / K — completed job missing terminal fields
    def test_completed_job_missing_completed_at(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row(completed_at=None))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "completed_job_missing_completed_at"

    def test_completed_job_missing_persisted_picks_timestamp(self):
        outcome, detail = self._validate(
            _valid_payload(), job_row=_valid_job_row(persisted_picks_timestamp=None))
        assert outcome == "rejected_malformed_base"
        assert detail["malformed_reason"] == "completed_job_missing_persisted_picks_timestamp"

    # Fully valid base
    def test_fresh_healthy_completed_base_passes(self):
        outcome, detail = self._validate(_valid_payload(), job_row=_valid_job_row())
        assert outcome is None
        assert detail["source_job_status"] == "completed"

    def test_never_mutates_input_payload(self):
        payload = _valid_payload()
        original = dict(payload)
        self._validate(payload, job_row=_valid_job_row())
        assert payload == original, "validate_base_for_finalization must not mutate its input"


# ─────────────────────────────────────────────────────────────────────────
# Part 2 — finalize_premarket(): integration behavior
# ─────────────────────────────────────────────────────────────────────────

_IN_WINDOW = datetime(2026, 7, 6, 8, 15, tzinfo=timezone.utc).astimezone(
    __import__("zoneinfo").ZoneInfo("America/New_York")
)
# Use a UTC instant that lands inside the window when converted to ET.
_IN_WINDOW_UTC = datetime(2026, 7, 6, 12, 15, tzinfo=timezone.utc)  # 8:15 AM EDT


def _postgres_env():
    return patch.dict("os.environ", {"USE_POSTGRES": "1"})


@pytest.mark.regression
class TestFinalizePremarketFreshBaseSuccess:
    @pytest.mark.asyncio
    async def test_fresh_healthy_base_is_finalized(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True) as mock_save, \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "completed"
        assert result["reason"] == "finalized"
        assert result["source_job_id"] == "job-us-1"
        assert result["base_universe_degraded"] is False
        assert "premarket_warnings" not in result
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_degraded_base_finalizes_with_explicit_warning(self):
        from services.premarket_finalizer import finalize_premarket
        degraded_payload = _valid_payload(universe_degraded=True)
        with patch("services.daily_picks.get_cached_picks", return_value=degraded_payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "completed"
        assert result["reason"] == "finalized"
        assert result["base_universe_degraded"] is True
        assert result["premarket_warnings"] == ["degraded_base_universe"]

    @pytest.mark.asyncio
    async def test_degraded_base_does_not_change_picks_or_scoring_fields(self):
        """A degraded-but-valid base must never be treated as equivalent to
        stale/malformed data, and must never mutate ranking/scoring."""
        from services.premarket_finalizer import finalize_premarket
        payload = _valid_payload(universe_degraded=True)
        payload["picks"]["short"][0].update(
            {"confidence": 82, "signal": "BUY", "target": 250.0, "stop_loss": 190.0})
        original_pick = dict(payload["picks"]["short"][0])
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            await finalize_premarket("US", now=_IN_WINDOW_UTC)
        for key, value in original_pick.items():
            assert payload["picks"]["short"][0][key] == value


@pytest.mark.regression
class TestFinalizePremarketIdempotency:
    @pytest.mark.asyncio
    async def test_exact_same_base_already_finalized(self):
        from services.premarket_finalizer import finalize_premarket
        payload = _valid_payload()
        payload["premarket_finalized_for_job_id"] = "job-us-1"
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        payload["premarket_finalized_at"] = "2026-07-06T11:35:00+00:00"
        with patch("services.daily_picks.get_cached_picks", return_value=payload), _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)
        assert result["status"] == "skipped"
        assert result["reason"] == "already_finalized"

    @pytest.mark.asyncio
    async def test_new_same_day_base_job_remains_eligible(self):
        """A same-day base RE-run under a NEW source_job_id must be treated
        as a fresh finalization opportunity, not blocked by the prior
        finalization's date alone. A genuine generate_picks() re-run
        constructs a brand-new payload dict and never copies forward the
        previous run's premarket_finalized_for_job_id/base markers — so
        this is what a real same-day rerun payload actually looks like
        (see TestFinalizePremarketAdversarialFinalizationMarkers for the
        separate, deliberately-adversarial case where those markers ARE
        present but point at a different job/base)."""
        from services.premarket_finalizer import finalize_premarket
        payload = _valid_payload(source_job_id="job-us-2-rerun")
        assert "premarket_finalized_for_job_id" not in payload
        assert "premarket_finalized_for_base" not in payload
        job_row = _valid_job_row(job_id="job-us-2-rerun")
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=job_row), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)
        assert result["status"] == "completed"
        assert result["reason"] == "finalized"
        assert result["source_job_id"] == "job-us-2-rerun"

    @pytest.mark.asyncio
    async def test_tomorrows_new_base_is_eligible(self):
        """A payload with no prior finalization fields at all (a fresh
        day's base) must never be treated as already-finalized."""
        from services.premarket_finalizer import finalize_premarket
        payload = _valid_payload()
        assert "premarket_finalized_for_job_id" not in payload
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)
        assert result["status"] == "completed"


async def _run_and_assert_no_side_effects(payload, job_row=None,
                                           job_lookup_side_effect=None,
                                           expect_reason=None, expect_status=None):
    """Shared helper: runs finalize_premarket() and asserts zero provider
    calls, zero persistence calls, and — since payload is never expected
    to survive a reject/skip path mutated — that the input payload dict
    (including nested pick dicts) comes back byte-for-byte unchanged."""
    from services.premarket_finalizer import finalize_premarket
    original = copy.deepcopy(payload) if payload is not None else None
    get_quote_mock = AsyncMock()
    get_context_mock = MagicMock()
    save_mock = MagicMock()
    open_mock = MagicMock()
    job_lookup_kwargs = {"side_effect": job_lookup_side_effect} if job_lookup_side_effect \
        else {"return_value": job_row}
    with patch("services.daily_picks.get_cached_picks", return_value=payload), \
         patch("services.postgres_store.get_daily_picks_job_by_id", **job_lookup_kwargs), \
         patch("services.postgres_store.save_picks_to_db", save_mock), \
         patch("services.market_data.MarketDataService.get_quote", get_quote_mock), \
         patch("services.global_context.get_global_context", get_context_mock), \
         patch("builtins.open", open_mock), \
         _postgres_env():
        result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

    get_quote_mock.assert_not_called()
    get_context_mock.assert_not_called()
    save_mock.assert_not_called()
    open_mock.assert_not_called()
    if payload is not None:
        assert payload == original, "payload must never be mutated on a reject/skip path"
    if expect_reason:
        assert result["reason"] == expect_reason
    if expect_status:
        assert result["status"] == expect_status
    return result


@pytest.mark.regression
class TestFinalizePremarketNoSideEffectsOnRejectOrSkip:
    """Every non-finalizing outcome must make zero provider calls and zero
    persistence calls — the base must be validated fully before any
    premarket provider work or write begins."""

    async def _run_and_assert_no_side_effects(self, payload, job_row=None,
                                               job_lookup_side_effect=None,
                                               expect_reason=None):
        return await _run_and_assert_no_side_effects(
            payload, job_row=job_row, job_lookup_side_effect=job_lookup_side_effect,
            expect_reason=expect_reason)

    @pytest.mark.asyncio
    async def test_no_base_makes_zero_side_effects(self):
        await self._run_and_assert_no_side_effects(None, expect_reason="skipped_no_base")

    @pytest.mark.asyncio
    async def test_stale_base_makes_zero_side_effects(self):
        payload = _valid_payload(generated_at="2026-07-05T04:10:00+00:00")
        await self._run_and_assert_no_side_effects(payload, expect_reason="skipped_stale_base")

    @pytest.mark.asyncio
    async def test_missing_source_job_id_makes_zero_side_effects(self):
        payload = _valid_payload(source_job_id=None)
        await self._run_and_assert_no_side_effects(payload, expect_reason="rejected_malformed_base")

    @pytest.mark.asyncio
    async def test_job_not_found_makes_zero_side_effects(self):
        payload = _valid_payload()
        await self._run_and_assert_no_side_effects(payload, job_row=None,
                                                     expect_reason="rejected_malformed_base")

    @pytest.mark.asyncio
    async def test_job_running_makes_zero_side_effects(self):
        payload = _valid_payload()
        job_row = _valid_job_row(status="running")
        await self._run_and_assert_no_side_effects(payload, job_row=job_row,
                                                     expect_reason="skipped_base_running")

    @pytest.mark.asyncio
    async def test_job_failed_makes_zero_side_effects(self):
        payload = _valid_payload()
        job_row = _valid_job_row(status="failed")
        await self._run_and_assert_no_side_effects(payload, job_row=job_row,
                                                     expect_reason="skipped_base_failed")

    @pytest.mark.asyncio
    async def test_job_lookup_error_makes_zero_side_effects(self):
        payload = _valid_payload()
        await self._run_and_assert_no_side_effects(
            payload, job_lookup_side_effect=Exception("DB down"),
            expect_reason="skipped_base_state_unavailable")


@pytest.mark.regression
class TestFinalizePremarketAdversarialFinalizationMarkers:
    """
    Scheduler Remediation Phase 1A post-review fix: exact-base idempotency
    must never rubber-stamp a stale, malformed, wrong-market, or
    unverifiable base merely because it carries a self-consistent-looking
    (or even copied/tampered) finalization-marker pair. Every case here
    independently reproduces the confirmed blocker's scenario shape and
    asserts the base is fully re-validated — zero job lookup, zero
    provider calls, zero persistence calls, payload unchanged — rather
    than short-circuited via already_finalized.
    """

    # A — stale but self-consistent finalized payload
    @pytest.mark.asyncio
    async def test_stale_payload_with_self_consistent_markers_is_not_already_finalized(self):
        """A prior-ET-day payload whose OWN finalization markers correctly
        describe itself (exactly what a genuinely stale base looks like,
        since these fields are only ever written as a self-consistent set)
        must still be re-validated for freshness, not fast-pathed."""
        stale_generated_at = "2026-07-05T07:30:00+00:00"  # yesterday in ET
        payload = _valid_payload(
            generated_at=stale_generated_at, source_job_id="job-us-yesterday")
        payload["premarket_finalized_for_job_id"] = "job-us-yesterday"
        payload["premarket_finalized_for_base"] = stale_generated_at
        payload["premarket_finalized_at"] = "2026-07-05T11:35:00+00:00"
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="skipped_stale_base", expect_status="skipped")
        assert result["reason"] != "already_finalized"

    # B — malformed generated_at with self-consistent raw marker strings
    @pytest.mark.asyncio
    async def test_malformed_generated_at_with_matching_markers_is_rejected(self):
        payload = _valid_payload(generated_at="not-a-real-timestamp")
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = "not-a-real-timestamp"
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")
        assert result["reason"] != "already_finalized"

    # C — wrong-market payload with matching markers
    @pytest.mark.asyncio
    async def test_wrong_market_payload_with_matching_markers_is_rejected(self):
        payload = _valid_payload(market="IN")
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")
        assert result["reason"] != "already_finalized"

    # D — missing source_job_id with copied/matching-looking markers
    @pytest.mark.asyncio
    async def test_missing_source_job_id_with_matching_markers_is_rejected(self):
        payload = _valid_payload(source_job_id=None)
        payload["premarket_finalized_for_job_id"] = "job-us-1"
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")
        assert result["reason"] != "already_finalized"

    # E — partial finalization metadata (only one of the pair present)
    @pytest.mark.asyncio
    async def test_only_job_id_marker_present_is_rejected(self):
        payload = _valid_payload()
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")

    @pytest.mark.asyncio
    async def test_only_base_marker_present_is_rejected(self):
        payload = _valid_payload()
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")

    # F — malformed premarket_finalized_for_base marker (job_id marker fine)
    @pytest.mark.asyncio
    async def test_malformed_finalization_marker_base_is_rejected(self):
        payload = _valid_payload()
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = "not-a-real-timestamp"
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")

    @pytest.mark.asyncio
    async def test_naive_finalization_marker_base_is_rejected(self):
        payload = _valid_payload()
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = "2026-07-06T04:10:00"  # no tzinfo
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")

    # G — valid but mismatched finalization-marker job ID
    @pytest.mark.asyncio
    async def test_mismatched_finalization_marker_job_id_is_rejected_not_treated_as_new_base(self):
        payload = _valid_payload(source_job_id="job-us-current")
        payload["premarket_finalized_for_job_id"] = "job-us-different"
        payload["premarket_finalized_for_base"] = payload["generated_at"]  # base DOES match
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")
        assert result["reason"] != "already_finalized"

    # H — valid but mismatched finalization-marker base timestamp
    @pytest.mark.asyncio
    async def test_mismatched_finalization_marker_base_is_rejected(self):
        payload = _valid_payload(source_job_id="job-us-current")
        payload["premarket_finalized_for_job_id"] = "job-us-current"  # job DOES match
        payload["premarket_finalized_for_base"] = "2026-07-06T01:00:00+00:00"  # different instant
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="rejected_malformed_base", expect_status="failed")
        assert result["reason"] != "already_finalized"

    # I — valid current-day exact-base second call: the preserved optimization
    @pytest.mark.asyncio
    async def test_valid_exact_base_second_call_is_already_finalized_with_zero_side_effects(self):
        payload = _valid_payload()
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        payload["premarket_finalized_at"] = "2026-07-06T11:35:00+00:00"
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="already_finalized", expect_status="skipped")

    # J — timestamp normalization: Z vs +00:00 must compare as the same instant
    @pytest.mark.asyncio
    async def test_z_suffix_generated_at_matches_plus_zero_offset_marker(self):
        payload = _valid_payload(generated_at="2026-07-06T04:10:00Z")
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = "2026-07-06T04:10:00+00:00"
        payload["premarket_finalized_at"] = "2026-07-06T11:35:00+00:00"
        result = await _run_and_assert_no_side_effects(
            payload, expect_reason="already_finalized", expect_status="skipped")


@pytest.mark.regression
class TestFinalizePremarketTruthfulPersistence:
    @pytest.mark.asyncio
    async def test_postgres_returns_false_reports_durable_persistence_failed(self):
        from services.premarket_finalizer import finalize_premarket
        open_mock = MagicMock()
        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=False), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             patch("builtins.open", open_mock), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "failed"
        assert result["reason"] == "durable_persistence_failed"
        open_mock.assert_not_called(), "disk must never be written when Postgres persistence fails"

    @pytest.mark.asyncio
    async def test_postgres_exception_reports_durable_persistence_failed(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", side_effect=Exception("db exploded")), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "failed"
        assert result["reason"] == "durable_persistence_failed"

    @pytest.mark.asyncio
    async def test_disk_cache_written_only_after_postgres_success(self):
        from services.premarket_finalizer import finalize_premarket
        call_order = []

        def fake_save(payload, market):
            call_order.append("postgres_save")
            return True

        real_open = open
        def fake_open(path, *a, **kw):
            call_order.append("disk_write")
            return real_open("/dev/null", *a, **kw)

        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", side_effect=fake_save), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             patch("builtins.open", side_effect=fake_open), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "completed"
        assert call_order == ["postgres_save", "disk_write"], \
            "Postgres save must happen before the disk cache is ever written"

    @pytest.mark.asyncio
    async def test_disk_failure_after_postgres_success_does_not_report_failure(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             patch("builtins.open", side_effect=OSError("disk full")), \
             _postgres_env():
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "completed"
        assert result["reason"] == "finalized"

    @pytest.mark.asyncio
    async def test_non_postgres_mode_fails_closed_rather_than_bypassing_verification(self):
        """Production code must not silently skip provenance verification
        merely because USE_POSTGRES is unexpectedly absent."""
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch.dict("os.environ", {"USE_POSTGRES": "0"}):
            result = await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert result["status"] == "skipped"
        assert result["reason"] == "skipped_base_state_unavailable"


@pytest.mark.regression
class TestFinalizePremarketTimestampConsistency:
    @pytest.mark.asyncio
    async def test_finalization_metadata_uses_the_injected_timestamp(self):
        from services.premarket_finalizer import finalize_premarket
        captured = {}

        def capture_save(payload, market):
            captured["payload"] = dict(payload)
            return True

        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", side_effect=capture_save), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            await finalize_premarket("US", now=_IN_WINDOW_UTC)

        expected_utc_iso = _IN_WINDOW_UTC.astimezone(timezone.utc).isoformat()
        assert captured["payload"]["premarket_finalized_at"] == expected_utc_iso
        assert captured["payload"]["premarket_window_checked"] == expected_utc_iso

    @pytest.mark.asyncio
    async def test_source_job_id_survives_save_call_unchanged(self):
        """source_job_id must reach save_picks_to_db byte-for-byte unchanged
        — daily_picks_cache.payload is unstructured JSONB with no field
        allowlist, so nothing strips or renames it on the way through."""
        from services.premarket_finalizer import finalize_premarket
        captured = {}

        def capture_save(payload, market):
            captured["payload"] = dict(payload)
            return True

        with patch("services.daily_picks.get_cached_picks", return_value=_valid_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_valid_job_row()), \
             patch("services.postgres_store.save_picks_to_db", side_effect=capture_save), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network")), \
             _postgres_env():
            await finalize_premarket("US", now=_IN_WINDOW_UTC)

        assert captured["payload"]["source_job_id"] == "job-us-1"
        assert captured["payload"]["premarket_finalized_for_job_id"] == "job-us-1"


# ─────────────────────────────────────────────────────────────────────────
# Part 3 — daily_picks.py payload construction: source_job_id provenance
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestGeneratePicksSourceJobIdProvenance:
    """
    Source-text assertions (this repo's established "code shape" test
    convention — see tests/regression/test_daily_picks_us_premarket_workflow.py)
    rather than a full _generate_picks_inner invocation: that function spans
    hundreds of lines across Phases 0-8 and mocking it end-to-end is
    disproportionate to verifying one payload field's exact source. This
    locks in the literal contract: source_job_id must be set from the
    job_id parameter, never a separately generated identifier.
    """
    def _daily_picks_source(self):
        import inspect
        import services.daily_picks as dp
        return inspect.getsource(dp)

    def test_payload_source_job_id_assigned_from_job_id_parameter(self):
        src = self._daily_picks_source()
        assert '"source_job_id":     job_id,' in src

    def test_payload_source_job_id_is_not_the_alpha_observation_fallback_id(self):
        """_alpha_run_id (job_id or a freshly generated uuid4 fallback) exists
        for the Alpha Observations run-id, a different concern — it must
        never be used as cache/premarket-finalizer provenance, since the
        fallback branch fires even when job_id is None (test/local runs)."""
        src = self._daily_picks_source()
        assert '"source_job_id":     _alpha_run_id' not in src
        assert '_alpha_run_id = job_id or str(_uuid.uuid4())' in src

    def test_get_daily_picks_job_by_id_exists_and_is_read_only(self):
        import inspect
        import services.postgres_store as ps
        src = inspect.getsource(ps.get_daily_picks_job_by_id)
        assert "SELECT" in src
        assert "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src
        assert "WHERE job_id = %s" in src
