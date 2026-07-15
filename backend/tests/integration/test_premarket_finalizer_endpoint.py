"""
3-phase US Daily Picks upgrade, Phase 2 — POST /api/picks/premarket-finalize
and services.premarket_finalizer.finalize_premarket().

Daily Picks Scheduler Remediation Phase 1A: base payloads used in these
tests now carry source_job_id and, where finalization is expected to
succeed, a mocked get_daily_picks_job_by_id() returning a matching
completed/persisted US job row — the new fail-closed provenance contract
(see services/premarket_finalizer.py and
tests/regression/test_premarket_finalizer_base_integrity.py for the full
validation-matrix coverage).

2026-07-15: fixtures and window-guard tests updated for the 6:00 AM ET
finalizer retargeting (was ~7:35 AM ET) and the 6:00-7:30 AM ET acceptance
window (was 7:30-9:00 AM ET) — see premarket_finalizer.py's
in_premarket_window() docstring for the full history.

All tests are deterministic and fully mocked — no real DB, no external
providers, no Daily Picks generation runs, no live network. Matches this
repo's tests/integration convention: multiple in-process modules together,
still no live network/DB calls (SES-003).
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "premarket-test-picks-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", TEST_SECRET)
    import importlib
    import api.routers.picks as picks_router
    importlib.reload(picks_router)
    from api.main import app
    return TestClient(app)


def _base_payload(with_picks=True, generated_at="2026-07-06T06:10:00+00:00",
                   source_job_id="job-us-fresh"):
    picks = {
        "short": [{"symbol": "AAPL", "price": 200.0, "confidence": 70, "signal": "BUY"}],
        "medium": [],
        "long": [],
    } if with_picks else {"short": [], "medium": [], "long": []}
    return {
        "generated_at": generated_at,
        "market": "US",
        "picks": picks,
        "source_job_id": source_job_id,
    }


def _completed_job_row(job_id="job-us-fresh", *, completed_at="2026-07-06T06:00:00+00:00",
                        persisted_picks_timestamp="2026-07-06T06:00:01+00:00"):
    """A durable job row satisfying every provenance check (G-K)."""
    return {
        "job_id": job_id,
        "market": "US",
        "status": "completed",
        "started_at": "2026-07-06T02:00:00+00:00",
        "completed_at": completed_at,
        "persisted_picks_timestamp": persisted_picks_timestamp,
        "universe_degraded": False,
        "last_error": None,
    }


# ── Endpoint auth ─────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPremarketFinalizeEndpointAuth:
    def test_missing_secret_is_rejected(self, client):
        resp = client.post("/api/picks/premarket-finalize", params={"market": "US"})
        assert resp.status_code == 401

    def test_wrong_secret_is_rejected(self, client):
        resp = client.post("/api/picks/premarket-finalize", params={"market": "US"},
                            headers={"x-secret": "wrong"})
        assert resp.status_code == 401

    def test_correct_secret_is_accepted(self, client):
        with patch("services.premarket_finalizer.finalize_premarket",
                   new=AsyncMock(return_value={"market": "US", "status": "skipped",
                                                "reason": "outside_premarket_finalizer_window"})):
            resp = client.post("/api/picks/premarket-finalize", params={"market": "US"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"


# ── Market gating ──────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPremarketFinalizeMarketGate:
    @pytest.mark.asyncio
    async def test_non_us_market_is_a_safe_noop(self):
        from services.premarket_finalizer import finalize_premarket
        result = await finalize_premarket("IN")
        assert result == {"market": "IN", "status": "skipped", "reason": "unsupported_market"}


# ── Window guard (service-level, deterministic via injected `now`) ────────────

@pytest.mark.integration
class TestPremarketFinalizeWindowGuard:
    @pytest.mark.asyncio
    async def test_outside_window_is_a_safe_noop(self):
        from services.premarket_finalizer import finalize_premarket
        # 20:00 UTC on a July weekday is nowhere near 6:00-7:30 AM ET.
        now = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)
        result = await finalize_premarket("US", now=now)
        assert result == {"market": "US", "status": "skipped",
                           "reason": "outside_premarket_finalizer_window"}

    @pytest.mark.asyncio
    async def test_edt_cron_candidate_runs_during_edt(self):
        """10:00 UTC maps to 6:00 AM New York during EDT (July) — allowed."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc)
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_completed_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network in test")), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now)
        assert result["status"] == "completed"
        assert result["reason"] == "finalized"

    @pytest.mark.asyncio
    async def test_est_cron_candidate_runs_during_est(self):
        """11:00 UTC maps to 6:00 AM New York during EST (January) — allowed."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 1, 6, 11, 0, tzinfo=timezone.utc)
        # EST is UTC-5 in January (vs. UTC-4 EDT in July) — 06:10 UTC would
        # land on the PRIOR ET calendar day (01:10 ET Jan 6 is fine, but the
        # base must still be same-day) — this fixture uses 11:10 UTC =
        # 6:10 AM ET, the same ET day as `now` (6:00 AM ET).
        payload = _base_payload(generated_at="2026-01-06T11:10:00+00:00")
        job_row = _completed_job_row(completed_at="2026-01-06T11:00:00+00:00",
                                      persisted_picks_timestamp="2026-01-06T11:00:01+00:00")
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=job_row), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network in test")), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now)
        assert result["status"] == "completed"
        assert result["reason"] == "finalized"

    @pytest.mark.asyncio
    async def test_est_candidate_is_now_also_inside_window_during_edt_but_idempotency_guard_skips_it(self):
        """
        11:00 UTC during EDT (July) is 7:00 AM ET — still inside the
        6:00-7:30 window (the same behavior the 2026-07-13 widened window
        already had, just retargeted earlier). This is the expected
        behavior: both candidates land in-window, so it's the exact-base
        idempotency guard — not the window boundary — that prevents this
        from being a second, duplicate finalization once the EDT candidate
        (10:00 UTC) has already run earlier that same morning.
        """
        from services.premarket_finalizer import finalize_premarket
        already_finalized_at = datetime(2026, 7, 6, 10, 0, tzinfo=timezone.utc).isoformat()
        payload = _base_payload()
        payload["premarket_finalized_at"] = already_finalized_at
        payload["premarket_finalized_for_job_id"] = payload["source_job_id"]
        payload["premarket_finalized_for_base"] = payload["generated_at"]
        now = datetime(2026, 7, 6, 11, 0, tzinfo=timezone.utc)
        with patch("services.daily_picks.get_cached_picks", return_value=payload), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=now)
        assert result["status"] == "skipped"
        assert result["reason"] == "already_finalized"

    @pytest.mark.asyncio
    async def test_edt_cron_candidate_noops_during_est(self):
        """10:00 UTC during EST (January) is 5:00 AM ET — still outside the
        window on this side (EST shifts it earlier, not later), so this one
        remains a genuine window no-op, not an idempotency-guard skip."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
        result = await finalize_premarket("US", now=now)
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_premarket_finalizer_window"


# ── Missing base picks / limited data — never crashes ──────────────────────────

@pytest.mark.integration
class TestPremarketFinalizeDataAvailability:
    # 6:15 AM America/New_York local time, directly — not a UTC conversion,
    # to avoid an off-by-offset bug that would silently land outside the window.
    _IN_WINDOW = datetime(2026, 7, 6, 6, 15, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))

    @pytest.mark.asyncio
    async def test_missing_base_picks_returns_safe_skipped_status(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=None):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "skipped"
        assert result["reason"] == "skipped_no_base"

    @pytest.mark.asyncio
    async def test_empty_picks_payload_returns_safe_skipped_status(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks",
                   return_value={"generated_at": "2026-07-06T06:10:00+00:00",
                                 "market": "US", "picks": {}}):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "skipped"
        assert result["reason"] == "skipped_no_base"

    @pytest.mark.asyncio
    async def test_missing_premarket_data_completes_with_limited_status_not_a_crash(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_completed_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(side_effect=Exception("provider down"))), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("provider down")), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "completed"
        assert result["reason"] == "finalized"
        assert result["premarket_data_available"] is False
        assert "price_gap_pct" in result["premarket_missing_inputs"]
        assert "index_proxy_direction" in result["premarket_missing_inputs"]
        # Always-missing inputs (no existing provider) must be reported honestly.
        for field in ("premarket_volume", "fresh_news_since_generation",
                      "sector_movement", "earnings_event_risk",
                      "abnormal_volatility_spread_risk"):
            assert field in result["premarket_missing_inputs"]

    @pytest.mark.asyncio
    async def test_available_price_gap_marks_pick_kept_with_reason(self):
        from services.premarket_finalizer import finalize_premarket
        quote = {"symbol": "AAPL", "market": "US", "price": 201.0}
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_completed_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(return_value=quote)), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("no network in test")), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "completed"
        assert result["premarket_data_available"] is True

    @pytest.mark.asyncio
    async def test_never_mutates_scoring_or_ranking_fields(self):
        """The finalizer must only ADD premarket_*/base_* keys — base pick
        fields (price, confidence, signal) must be byte-for-byte unchanged."""
        from services.premarket_finalizer import finalize_premarket
        base = _base_payload()
        original_pick = dict(base["picks"]["short"][0])
        with patch("services.daily_picks.get_cached_picks", return_value=base), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.postgres_store.get_daily_picks_job_by_id", return_value=_completed_job_row()), \
             patch("services.postgres_store.save_picks_to_db", return_value=True), \
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("no network in test")), \
             patch.dict("os.environ", {"USE_POSTGRES": "1"}):
            await finalize_premarket("US", now=self._IN_WINDOW)
        finalized_pick = base["picks"]["short"][0]
        for key, value in original_pick.items():
            assert finalized_pick[key] == value, f"base field {key} was mutated"
        assert finalized_pick["premarket_action"] in {
            "keep", "upgrade", "downgrade", "replace_from_backup",
            "mark_premarket_risk", "skip_due_to_data_unavailable",
        }
        assert "premarket_reason" in finalized_pick


# ── Existing endpoints remain unchanged ─────────────────────────────────────────

@pytest.mark.integration
class TestExistingEndpointsUnaffected:
    def test_generate_endpoint_still_requires_secret(self, client):
        resp = client.post("/api/picks/generate", params={"market": "US"},
                            headers={"x-secret": "wrong"})
        assert resp.status_code == 401

    def test_generate_endpoint_still_accepts_valid_trigger(self, client, monkeypatch):
        import services.daily_picks as dp
        monkeypatch.setenv("USE_POSTGRES", "1")
        with patch.object(dp, "generate_picks"), \
             patch("services.postgres_store.try_reserve_daily_picks_job_with_lease", return_value="started"), \
             patch("services.postgres_store.release_heavy_workload_lease"), \
             patch("services.daily_picks.picks_generated_today", return_value=False):
            resp = client.post("/api/picks/generate", params={"market": "US"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code in (200, 202)

    def test_status_endpoint_backward_compatible_for_in(self, client):
        """IN status response must be byte-for-byte the same shape as before —
        no premarket_* fields leak into a market that has no premarket phase."""
        resp = client.get("/api/picks/status", params={"market": "IN"})
        assert resp.status_code == 200
        body = resp.json()
        assert "market" in body and "generating" in body and "has_today" in body
        assert "premarket_status" not in body
        assert "base_generated_at" not in body

    def test_status_endpoint_includes_premarket_fields_for_us(self, client):
        resp = client.get("/api/picks/status", params={"market": "US"})
        assert resp.status_code == 200
        body = resp.json()
        # Additive fields present (value may be None if no finalizer run yet
        # or Postgres unavailable in this test env — presence is what matters).
        for field in ("base_generated_at", "premarket_finalized_at",
                      "premarket_status", "premarket_finalizer_version",
                      "next_base_run_hint", "next_premarket_run_hint"):
            assert field in body
        # Existing fields must still be present — no regression.
        assert "market" in body and "generating" in body and "has_today" in body

    def test_status_hints_reflect_6am_et_schedule_not_stale_values(self, client):
        """SES-006 corrective release (2026-07-15): next_base_run_hint/
        next_premarket_run_hint must reflect the deployed 06:00 UTC base /
        6:00 AM ET premarket schedule, not the pre-corrective stale values
        that were blocked by an unrelated staged file in a prior phase."""
        resp = client.get("/api/picks/status", params={"market": "US"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["next_base_run_hint"] == "06:00 UTC / 10:00 AM Dubai / 11:30 AM IST, Mon-Fri"
        assert "6:00 AM America/New_York" in body["next_premarket_run_hint"]
        assert "10:00 UTC" in body["next_premarket_run_hint"]
        assert "11:00 UTC" in body["next_premarket_run_hint"]
        for stale in ("04:00 UTC", "8:00 AM Dubai", "9:30 AM IST", "8:05 AM",
                      "12:05 UTC", "13:05 UTC"):
            assert stale not in body["next_base_run_hint"]
            assert stale not in body["next_premarket_run_hint"]

    def test_us_empty_state_message_no_longer_claims_9am_et(self, client):
        """Empty-state message must not claim the base is generated at
        9 AM ET — that was never true and predates the 3-phase split."""
        with patch("services.daily_picks.get_cached_picks", return_value=None):
            resp = client.get("/api/picks/daily", params={"market": "US"})
        assert resp.status_code == 200
        body = resp.json()
        assert "9 AM ET" not in body["message"]
        assert "06:00 UTC" in body["message"]

    def test_generate_docstring_reflects_current_base_schedule(self):
        from api.routers.picks import trigger_generation
        doc = trigger_generation.__doc__ or ""
        assert "06:00 UTC" in doc
        assert "12:30 UTC" not in doc
        assert "8:30 AM ET" not in doc

    def test_premarket_finalize_docstring_reflects_6to730_window(self):
        from api.routers.picks import premarket_finalize
        doc = premarket_finalize.__doc__ or ""
        assert "execution window (6:00-7:30 AM America/New_York" in doc
        assert "execution window (7:30-9:00" not in doc
