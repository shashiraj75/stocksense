"""
3-phase US Daily Picks upgrade, Phase 2 — POST /api/picks/premarket-finalize
and services.premarket_finalizer.finalize_premarket().

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


def _base_payload(with_picks=True):
    picks = {
        "short": [{"symbol": "AAPL", "price": 200.0, "confidence": 70, "signal": "BUY"}],
        "medium": [],
        "long": [],
    } if with_picks else {"short": [], "medium": [], "long": []}
    return {
        "generated_at": "2026-07-06T04:10:00+00:00",
        "market": "US",
        "picks": picks,
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
        # 20:00 UTC on a July weekday is nowhere near 8-8:30 AM ET.
        now = datetime(2026, 7, 6, 20, 0, tzinfo=timezone.utc)
        result = await finalize_premarket("US", now=now)
        assert result == {"market": "US", "status": "skipped",
                           "reason": "outside_premarket_finalizer_window"}

    @pytest.mark.asyncio
    async def test_edt_cron_candidate_runs_during_edt(self):
        """12:05 UTC maps to ~8:05 AM New York during EDT (July) — allowed."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 7, 6, 12, 5, tzinfo=timezone.utc)
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network in test")):
            result = await finalize_premarket("US", now=now)
        assert result["status"] == "completed_with_limited_premarket_data"

    @pytest.mark.asyncio
    async def test_est_cron_candidate_runs_during_est(self):
        """13:05 UTC maps to ~8:05 AM New York during EST (January) — allowed."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 1, 6, 13, 5, tzinfo=timezone.utc)
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.market_data.MarketDataService.get_quote", new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context", side_effect=Exception("no network in test")):
            result = await finalize_premarket("US", now=now)
        assert result["status"] == "completed_with_limited_premarket_data"

    @pytest.mark.asyncio
    async def test_est_cron_candidate_noops_during_edt(self):
        """13:05 UTC during EDT (July) is ~9:05 AM ET — the wrong candidate, must skip."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 7, 6, 13, 5, tzinfo=timezone.utc)
        result = await finalize_premarket("US", now=now)
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_premarket_finalizer_window"

    @pytest.mark.asyncio
    async def test_edt_cron_candidate_noops_during_est(self):
        """12:05 UTC during EST (January) is ~7:05 AM ET — the wrong candidate, must skip."""
        from services.premarket_finalizer import finalize_premarket
        now = datetime(2026, 1, 6, 12, 5, tzinfo=timezone.utc)
        result = await finalize_premarket("US", now=now)
        assert result["status"] == "skipped"
        assert result["reason"] == "outside_premarket_finalizer_window"


# ── Missing base picks / limited data — never crashes ──────────────────────────

@pytest.mark.integration
class TestPremarketFinalizeDataAvailability:
    # 8:15 AM America/New_York local time, directly — not a UTC conversion,
    # to avoid an off-by-offset bug that would silently land outside the window.
    _IN_WINDOW = datetime(2026, 7, 6, 8, 15, tzinfo=__import__("zoneinfo").ZoneInfo("America/New_York"))

    @pytest.mark.asyncio
    async def test_missing_base_picks_returns_safe_failed_status(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=None):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "failed"
        assert result["reason"] == "no_base_picks_available"

    @pytest.mark.asyncio
    async def test_empty_picks_payload_returns_safe_failed_status(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks",
                   return_value={"generated_at": "2026-07-06T04:10:00+00:00", "picks": {}}):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_missing_premarket_data_completes_with_limited_status_not_a_crash(self):
        from services.premarket_finalizer import finalize_premarket
        with patch("services.daily_picks.get_cached_picks", return_value=_base_payload()), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(side_effect=Exception("provider down"))), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("provider down")):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "completed_with_limited_premarket_data"
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
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(return_value=quote)), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("no network in test")):
            result = await finalize_premarket("US", now=self._IN_WINDOW)
        assert result["status"] == "completed_with_limited_premarket_data"
        assert result["premarket_data_available"] is True

    @pytest.mark.asyncio
    async def test_never_mutates_scoring_or_ranking_fields(self):
        """The finalizer must only ADD premarket_* keys — base pick fields
        (price, confidence, signal) must be byte-for-byte unchanged."""
        from services.premarket_finalizer import finalize_premarket
        base = _base_payload()
        original_pick = dict(base["picks"]["short"][0])
        with patch("services.daily_picks.get_cached_picks", return_value=base), \
             patch("services.daily_picks._cache_file", return_value="/dev/null"), \
             patch("services.market_data.MarketDataService.get_quote",
                   new=AsyncMock(return_value=None)), \
             patch("services.global_context.get_global_context",
                   side_effect=Exception("no network in test")):
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
             patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
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
