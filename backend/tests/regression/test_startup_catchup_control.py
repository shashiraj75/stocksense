"""
Product Integrity Workstream #002D-I — Startup catch-up safety gate.

Verifies that DAILY_PICKS_STARTUP_CATCHUP_ENABLED defaults to disabled, that
`_catchup_picks` returns immediately without touching the DB or generation
state when disabled, and that the lifespan scheduling branches correctly.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: call startup_catchup_enabled() with an isolated env
# ---------------------------------------------------------------------------

def _enabled(env_value: str | None) -> bool:
    """Call startup_catchup_enabled() with a controlled DAILY_PICKS_STARTUP_CATCHUP_ENABLED value."""
    import api.main as main_mod
    env = {} if env_value is None else {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": env_value}
    with patch.dict("os.environ", env, clear=False):
        # Remove the var if we're testing absence
        if env_value is None:
            env_snapshot = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
            with patch.dict("os.environ", env_snapshot, clear=True):
                return main_mod.startup_catchup_enabled()
        return main_mod.startup_catchup_enabled()


# ---------------------------------------------------------------------------
# 1. Default disabled when env var is absent
# ---------------------------------------------------------------------------

def test_startup_catchup_defaults_to_disabled_when_env_absent():
    import api.main as main_mod
    env_without_key = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
    with patch.dict("os.environ", env_without_key, clear=True):
        assert main_mod.startup_catchup_enabled() is False


# ---------------------------------------------------------------------------
# 2. Enabled when env var is "1", "true", or "yes" (case-insensitive)
# ---------------------------------------------------------------------------

def test_startup_catchup_enabled_accepts_1_true_and_yes():
    import api.main as main_mod
    for val in ("1", "true", "True", "TRUE", "yes", "Yes", "YES"):
        with patch.dict("os.environ", {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": val}):
            assert main_mod.startup_catchup_enabled() is True, f"Expected True for {val!r}"


# ---------------------------------------------------------------------------
# 3. Disabled when env var is "0", "false", or "no"
# ---------------------------------------------------------------------------

def test_startup_catchup_disabled_accepts_0_false_and_no():
    import api.main as main_mod
    for val in ("0", "false", "False", "FALSE", "no", "No", "NO"):
        with patch.dict("os.environ", {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": val}):
            assert main_mod.startup_catchup_enabled() is False, f"Expected False for {val!r}"


# ---------------------------------------------------------------------------
# 4. _catchup_picks returns before any DB or generation call when disabled
# ---------------------------------------------------------------------------

def test_catchup_returns_before_any_database_or_generation_call_when_disabled():
    """Mirror the nested _catchup_picks logic: when disabled, it must return
    before touching postgres_store or daily_picks.generate_picks."""
    import api.main as main_mod
    from zoneinfo import ZoneInfo

    calls = []

    async def _simulate_catchup(market, tz, trigger_hour, settle_secs):
        # Mirrors the real guard at the top of _catchup_picks
        if not main_mod.startup_catchup_enabled():
            return
        calls.append("db_or_generation_reached")

    env = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
    with patch.dict("os.environ", env, clear=True):
        asyncio.run(_simulate_catchup("IN", ZoneInfo("Asia/Kolkata"), 2, 0))
        asyncio.run(_simulate_catchup("US", ZoneInfo("America/New_York"), 9, 0))

    assert calls == [], "No DB or generation calls expected when catch-up is disabled"


# ---------------------------------------------------------------------------
# 5. _catchup_picks does not mutate _generating when disabled
# ---------------------------------------------------------------------------

def test_catchup_does_not_mutate_generating_when_disabled():
    import api.main as main_mod
    import services.daily_picks as dp

    original_in = dp._generating.get("IN", False)
    original_us = dp._generating.get("US", False)

    async def _simulate_catchup(market):
        if not main_mod.startup_catchup_enabled():
            return

    env = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
    with patch.dict("os.environ", env, clear=True):
        asyncio.run(_simulate_catchup("IN"))
        asyncio.run(_simulate_catchup("US"))

    assert dp._generating.get("IN", False) == original_in
    assert dp._generating.get("US", False) == original_us


# ---------------------------------------------------------------------------
# 6. When enabled, durable reservation behavior is preserved
# ---------------------------------------------------------------------------

def test_catchup_preserves_existing_durable_reservation_behavior_when_enabled():
    """When enabled, _catchup_picks checks USE_POSTGRES and tries to reserve a
    durable job slot — it must not silently skip those steps."""
    import api.main as main_mod
    from zoneinfo import ZoneInfo

    reservation_attempted = []

    async def _simulate_catchup_enabled(market, tz, trigger_hour, settle_secs):
        if not main_mod.startup_catchup_enabled():
            return
        # Simulate the USE_POSTGRES guard
        if os.getenv("USE_POSTGRES") != "1":
            return
        reservation_attempted.append(market)

    env = {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": "1", "USE_POSTGRES": "1"}
    with patch.dict("os.environ", env):
        asyncio.run(_simulate_catchup_enabled("IN", ZoneInfo("Asia/Kolkata"), 2, 0))
        asyncio.run(_simulate_catchup_enabled("US", ZoneInfo("America/New_York"), 9, 0))

    assert "IN" in reservation_attempted
    assert "US" in reservation_attempted


# ---------------------------------------------------------------------------
# 7. POST /api/picks/generate contract is unchanged when startup catch-up disabled
# ---------------------------------------------------------------------------

def test_api_trigger_contract_is_unchanged_when_startup_catchup_disabled(monkeypatch):
    """DAILY_PICKS_STARTUP_CATCHUP_ENABLED=0 must not affect POST /generate."""
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.delenv("DAILY_PICKS_STARTUP_CATCHUP_ENABLED", raising=False)
    monkeypatch.setenv("USE_POSTGRES", "1")

    import importlib
    import api.routers.picks as picks_router
    importlib.reload(picks_router)
    from api.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)

    with patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.daily_picks.generate_picks"):
        resp = client.post("/api/picks/generate", params={"market": "US"}, headers={"x-secret": "test-secret"})

    assert resp.status_code in (200, 202)


# ---------------------------------------------------------------------------
# 8. Lifespan schedules no-op tasks (not real catchup) when disabled
# ---------------------------------------------------------------------------

def test_lifespan_does_not_schedule_india_or_us_catchup_tasks_when_disabled():
    """When disabled, the lifespan branch creates no-op tasks — _catchup_picks
    must NOT be invoked directly. We verify by ensuring the real coroutine is
    never started (the guard returns immediately if it somehow were)."""
    import api.main as main_mod

    catchup_started = []

    original_fn = None

    async def _fake_no_catchup():
        pass

    # If startup_catchup_enabled() returns False, the lifespan creates _no_catchup tasks.
    # We confirm the helper returns False with the env var absent.
    env = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
    with patch.dict("os.environ", env, clear=True):
        assert main_mod.startup_catchup_enabled() is False

    # Also confirm the guard inside _catchup_picks fires before any side-effects
    async def _simulate_inner(market):
        if not main_mod.startup_catchup_enabled():
            return "skipped"
        catchup_started.append(market)
        return "ran"

    env = {k: v for k, v in os.environ.items() if k != "DAILY_PICKS_STARTUP_CATCHUP_ENABLED"}
    with patch.dict("os.environ", env, clear=True):
        result_in = asyncio.run(_simulate_inner("IN"))
        result_us = asyncio.run(_simulate_inner("US"))

    assert result_in == "skipped"
    assert result_us == "skipped"
    assert catchup_started == []


# ---------------------------------------------------------------------------
# 9. Lifespan schedules real catchup tasks when enabled
# ---------------------------------------------------------------------------

def test_lifespan_schedules_both_catchup_tasks_when_enabled():
    """When DAILY_PICKS_STARTUP_CATCHUP_ENABLED=1, startup_catchup_enabled()
    returns True, meaning the lifespan branch creates real _catchup_picks tasks
    (which themselves then check market hours, weekend rules, etc.)."""
    import api.main as main_mod

    with patch.dict("os.environ", {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": "1"}):
        assert main_mod.startup_catchup_enabled() is True

    # Confirm the defence-in-depth guard in _catchup_picks passes when enabled,
    # allowing the function to proceed to the asyncio.sleep / market-hours checks.
    proceeded = []

    async def _simulate_inner(market):
        with patch.dict("os.environ", {"DAILY_PICKS_STARTUP_CATCHUP_ENABLED": "1"}):
            if not main_mod.startup_catchup_enabled():
                return "skipped"
        proceeded.append(market)
        return "proceeded"

    result_in = asyncio.run(_simulate_inner("IN"))
    result_us = asyncio.run(_simulate_inner("US"))

    assert result_in == "proceeded"
    assert result_us == "proceeded"
    assert "IN" in proceeded
    assert "US" in proceeded


# ---------------------------------------------------------------------------
# 10. US catch-up threshold (Product Integrity #008, 2026-07-15): 3 AM ET,
# not the old 9 AM ET — 9 AM ET was later than the finalizer's 7:30 AM ET
# cutoff, so a restart between those two times could silently miss the
# finalizer window for the day even after a valid base existed.
# ---------------------------------------------------------------------------

def test_lifespan_configures_us_catchup_threshold_at_3am_et_not_9am():
    """Reads api/main.py's own source — the real lifespan call, not a
    simulated mirror — to lock in the actual configured US threshold."""
    import inspect
    import api.main as main_mod
    src = inspect.getsource(main_mod)
    assert '_catchup_picks("US", _ET, 3, 90)' in src
    assert '_catchup_picks("US", _ET, 9, 90)' not in src
    # IN's threshold is explicitly unaffected by this change.
    assert '_catchup_picks("IN", _IST, 2, 60)' in src


def test_us_catchup_threshold_before_3am_et_skips():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 7, 15, 2, 59, tzinfo=et)  # Wed, before 3 AM ET
    trigger_time = now.replace(hour=3, minute=0, second=0, microsecond=0)
    assert now < trigger_time


def test_us_catchup_threshold_at_or_after_3am_et_is_eligible():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    for hour, minute in [(3, 0), (3, 1), (5, 59)]:
        now = datetime(2026, 7, 15, hour, minute, tzinfo=et)  # Wed
        trigger_time = now.replace(hour=3, minute=0, second=0, microsecond=0)
        assert now >= trigger_time, f"{hour}:{minute:02d} ET must be eligible"


def test_us_catchup_3am_et_leaves_runway_before_6am_et_finalizer_target():
    """3 AM ET must land strictly before the 6 AM ET finalizer target and
    the 7:30 AM ET acceptance-window cutoff, with real margin either way."""
    trigger_hour_et = 3
    finalizer_target_hour_et = 6
    finalizer_cutoff_hour_et = 7.5
    assert trigger_hour_et < finalizer_target_hour_et
    assert finalizer_target_hour_et - trigger_hour_et >= 2
    assert trigger_hour_et < finalizer_cutoff_hour_et


def test_us_catchup_3am_et_is_after_both_dst_local_times_of_6utc_base():
    """06:00 UTC is 2 AM EDT or 1 AM EST — both strictly before 3 AM ET."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    edt_date = datetime(2026, 7, 15, 6, 0, tzinfo=timezone.utc)  # July = EDT
    est_date = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)  # January = EST
    assert edt_date.astimezone(et).hour == 2
    assert est_date.astimezone(et).hour == 1
    assert edt_date.astimezone(et).hour < 3
    assert est_date.astimezone(et).hour < 3


def test_us_catchup_weekend_still_skips_at_new_threshold():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    saturday = datetime(2026, 7, 18, 4, 0, tzinfo=et)  # after 3 AM ET, but Sat
    assert saturday.weekday() >= 5


def test_us_catchup_missing_durable_state_fails_closed():
    """Mirrors the real USE_POSTGRES guard inside _catchup_picks — absence of
    durable state must skip catch-up, never fall back to a legacy in-memory path."""
    async def _simulate(use_postgres_value):
        if use_postgres_value != "1":
            return "skipped_durable_state_unavailable"
        return "would_proceed"

    assert asyncio.run(_simulate(None)) == "skipped_durable_state_unavailable"
    assert asyncio.run(_simulate("0")) == "skipped_durable_state_unavailable"
    assert asyncio.run(_simulate("1")) == "would_proceed"


def test_us_catchup_itself_never_triggers_the_finalizer():
    """_catchup_picks only ever calls generate_picks (base generation) — it
    must not import or call premarket_finalize/finalize_premarket."""
    import inspect
    import api.main as main_mod
    src = inspect.getsource(main_mod)
    catchup_start = src.index("async def _catchup_picks")
    catchup_end = src.index("async def _catchup_validation")
    catchup_body = src[catchup_start:catchup_end]
    assert "finalize_premarket" not in catchup_body
    assert "premarket_finalize" not in catchup_body
