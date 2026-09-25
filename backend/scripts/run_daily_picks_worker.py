#!/usr/bin/env python3
"""One-shot Daily Picks worker for Railway cron services.

Runs the existing production Daily Picks pipeline in a short-lived container,
using the same durable job reservation + heavy-workload lease contract as the
API trigger, then exits.  No scheduling logic lives here: Railway cron owns
the schedule and this process owns exactly one market/run.

The purpose of this entry point is resource isolation.  The long-running API
must not carry Daily Picks' pandas/numpy/yfinance/native-allocation high-water
mark between runs, and a Daily Picks run must not start with the API's existing
multi-GB container baseline.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Railway runs this as a file path (python scripts/run_daily_picks_worker.py).
# In that mode Python puts backend/scripts, not backend itself, at sys.path[0].
# Add the backend root explicitly before importing services.* so the worker is
# independent of shell/PYTHONPATH quirks.
_BACKEND_ROOT = str(Path(__file__).resolve().parents[1])
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from services.logging_config import configure_logging

configure_logging()
log = logging.getLogger("daily_picks_worker")

_VALID_MARKETS = {"IN", "US"}
_HEAVY_RESOURCE = {"IN": "IN_SCREENER_HEAVY", "US": "US_YFINANCE_HEAVY"}


def _market_from_argv(argv: list[str]) -> str:
    if len(argv) != 2:
        raise ValueError("usage: run_daily_picks_worker.py IN|US")
    market = argv[1].strip().upper()
    if market not in _VALID_MARKETS:
        raise ValueError(f"unsupported market {market!r}; expected IN or US")
    return market


def run(market: str) -> int:
    if os.getenv("USE_POSTGRES") != "1":
        log.error("[daily_picks_worker] USE_POSTGRES must be 1; refusing non-durable generation")
        return 2

    import services.daily_picks as dp
    from services.postgres_store import (
        _DAILY_PICKS_PERIODIC_STALE_INTERVAL,
        get_active_daily_picks_job,
        get_daily_picks_job_by_id,
        reconcile_stale_daily_picks_jobs,
        release_heavy_workload_lease,
        try_reserve_daily_picks_job_with_lease,
    )

    # A short-lived cron worker has no API-process startup reconciliation.
    # Reuse the proven heartbeat-aware stale-job reconciler before reserving.
    try:
        reclaimed = reconcile_stale_daily_picks_jobs(_DAILY_PICKS_PERIODIC_STALE_INTERVAL)
        if reclaimed:
            log.warning("[daily_picks_worker] [%s] reconciled %s stale job(s)", market, reclaimed)
    except Exception as exc:
        log.warning("[daily_picks_worker] [%s] stale-job reconciliation failed: %s", market, exc)

    if dp.picks_generated_today(market):
        log.info("[daily_picks_worker] [%s] today's successful picks already exist; no-op", market)
        return 0

    job_id = str(uuid.uuid4())
    resource = _HEAVY_RESOURCE[market]
    try:
        outcome = try_reserve_daily_picks_job_with_lease(
            job_id, market, dp._RUNNER_ID, resource
        )
    except Exception as exc:
        log.error("[daily_picks_worker] [%s] durable reservation failed: %s", market, exc)
        return 3

    if outcome == "already_running":
        active = None
        try:
            active = get_active_daily_picks_job(market)
        except Exception:
            pass
        log.warning(
            "[daily_picks_worker] [%s] another durable run is active (job_id=%s); no duplicate started",
            market,
            active.get("job_id") if active else None,
        )
        return 0

    if outcome == "resource_busy":
        log.error(
            "[daily_picks_worker] [%s] heavy resource %s is busy; scheduled run not started",
            market,
            resource,
        )
        return 4

    if outcome != "reserved":
        log.error("[daily_picks_worker] [%s] unexpected reservation outcome=%s", market, outcome)
        return 5

    try:
        log.info(
            "[daily_picks_worker] [%s] starting isolated scheduled run job_id=%s at %s",
            market,
            job_id,
            datetime.now(timezone.utc).isoformat(),
        )
        payload = dp.generate_picks(market, job_id=job_id)

        # generate_picks intentionally converts internal exceptions into a
        # failure payload so API callers remain stable.  A cron process must
        # surface that as a non-zero exit so Railway marks the scheduled
        # execution failed and operators can see it immediately.
        row = None
        try:
            row = get_daily_picks_job_by_id(job_id)
        except Exception as exc:
            log.error("[daily_picks_worker] [%s] could not verify terminal job state: %s", market, exc)
            return 6

        status = row.get("status") if row else None
        if status != "completed" or (isinstance(payload, dict) and payload.get("error")):
            log.error(
                "[daily_picks_worker] [%s] generation did not complete successfully "
                "(job_id=%s status=%s)",
                market,
                job_id,
                status,
            )
            return 7

        log.info(
            "[daily_picks_worker] [%s] completed successfully job_id=%s at %s",
            market,
            job_id,
            datetime.now(timezone.utc).isoformat(),
        )
        return 0
    finally:
        try:
            release_heavy_workload_lease(job_id)
        except Exception as exc:
            log.warning(
                "[daily_picks_worker] [%s] lease release failed for job_id=%s: %s",
                market,
                job_id,
                exc,
            )


def main() -> int:
    try:
        market = _market_from_argv(sys.argv)
    except ValueError as exc:
        log.error("[daily_picks_worker] %s", exc)
        return 64
    return run(market)


if __name__ == "__main__":
    raise SystemExit(main())
