import math
import os
import sys
import asyncio
import importlib
import logging
from contextlib import asynccontextmanager

from services.logging_config import configure_logging
configure_logging()

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from api.routers import stocks, predictions, news, screener, watchlist, backtest, picks, validation, paper_trading, alerts, auth, feedback, portfolio, multibagger, leadership
from services.rate_limit import limiter

log = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = 7 * 24 * 3600  # weekly


async def _refresh_universe():
    """Run the stock universe generator in a thread (non-blocking)."""
    try:
        # Add backend root to path so the script can import properly
        backend_root = os.path.dirname(os.path.dirname(__file__))
        if backend_root not in sys.path:
            sys.path.insert(0, backend_root)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _do_refresh)
    except Exception as e:
        log.warning(f"[universe] Background refresh error: {e}")


def _do_refresh():
    try:
        # Import fresh each time so edits to the script are picked up
        import importlib, scripts.generate_stock_universe as gen
        importlib.reload(gen)
        success = gen.run()
        if success:
            # Reload the universe module so the new data is live immediately
            import services.stock_universe as univ
            importlib.reload(univ)
            log.info("[universe] Reload complete — search list is up to date.")
    except Exception as e:
        log.warning(f"[universe] Refresh failed (existing list still active): {e}")


async def _weekly_refresh_loop():
    """Background task: refresh once on startup, then every 7 days."""
    await asyncio.sleep(30)          # let server fully start first
    while True:
        log.info("[universe] Starting scheduled refresh …")
        await _refresh_universe()
        log.info(f"[universe] Next refresh in {REFRESH_INTERVAL_SECONDS // 3600}h.")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _keepalive_loop():
    """
    Ping own /health every 14 minutes as a secondary keepalive fallback.
    Works on any platform — reads RAILWAY_PUBLIC_DOMAIN or SELF_URL env var.
    Railway doesn't sleep so this is just a safety net.
    """
    await asyncio.sleep(60)
    self_url = os.getenv("SELF_URL", "")
    if not self_url:
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if domain:
            self_url = f"https://{domain}"
    if not self_url:
        return
    url = f"{self_url}/health"
    while True:
        try:
            # Use asyncio subprocess so we never block the event loop
            proc = await asyncio.create_subprocess_exec(
                "curl", "-sf", "--max-time", "10", url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            log.info(f"[keepalive] pinged {url}")
        except Exception as e:
            log.warning(f"[keepalive] ping failed: {e}")
        await asyncio.sleep(14 * 60)


async def _yfinance_crumb_loop():
    """Refresh yfinance session crumb every 40 minutes — prevents 401 Invalid Crumb errors.
    First refresh at 40 min (not 90) so a ~60-min Yahoo session TTL is always covered."""
    await asyncio.sleep(40 * 60)
    while True:
        try:
            import yfinance as yf
            loop = asyncio.get_event_loop()
            def _do():
                if hasattr(yf.utils, "get_crumb"):
                    yf.utils.get_crumb(force=True)
                yf.Ticker("RELIANCE.NS").fast_info  # warm with an IN ticker too
            await loop.run_in_executor(None, _do)
            log.info("[crumb] yfinance session refreshed")
        except Exception as e:
            log.warning(f"[crumb] refresh failed (non-fatal): {e}")
        await asyncio.sleep(40 * 60)


async def _outcome_resolver_loop():
    """Resolve pending predictions against actual returns every 6 hours."""
    await asyncio.sleep(120)  # let server fully start first
    while True:
        try:
            loop = asyncio.get_event_loop()
            from services.alpha_engine.outcome_logger import resolve_pending_outcomes
            await loop.run_in_executor(None, resolve_pending_outcomes)
        except Exception as e:
            log.warning(f"[outcome_resolver] error: {e}")
        await asyncio.sleep(6 * 3600)


async def _paper_trade_notify_loop():
    """Email a paper trade's owner when its live price nears the target or stop loss."""
    await asyncio.sleep(150)  # let server fully start first
    while True:
        try:
            loop = asyncio.get_event_loop()
            from services.trade_notifier import check_and_notify
            await loop.run_in_executor(None, check_and_notify)
        except Exception as e:
            log.warning(f"[trade_notifier] error: {e}")
        await asyncio.sleep(15 * 60)  # every 15 minutes


async def _paper_trade_exit_monitor_loop():
    """2026-09 root-cause fix: Auto Close trade triggering used to be
    entirely client-side (a trade only closed while a browser tab had its
    row mounted and polling). This loop supplies the missing server-side
    trigger — every cycle, closes any OPEN trade_management_mode='auto'
    trade whose live price has hit its stop-loss or target, through the
    same authoritative close_paper_trade path the manual sell endpoint
    uses. Shorter interval than _paper_trade_notify_loop (5 min vs 15 min)
    since actually closing a triggered position is more time-sensitive
    than a proximity email; _notify_auto_close_triggers (in the loop
    above) picks up and emails about trades this loop closes, unchanged."""
    await asyncio.sleep(200)  # distinct stagger from the notify loop's 150s
    while True:
        try:
            loop = asyncio.get_event_loop()
            from services.paper_trade_exit_monitor import run_exit_monitor_cycle
            summary = await loop.run_in_executor(None, run_exit_monitor_cycle)
            if summary.get("closed") or summary.get("errors"):
                log.info(f"[paper_trade_exit_monitor] cycle summary: {summary}")
        except Exception as e:
            log.warning(f"[paper_trade_exit_monitor] error: {e}")
        await asyncio.sleep(5 * 60)  # every 5 minutes


async def _us_movers_refresh_loop():
    """
    Pre-warms the US Top Gainers/Losers cache with a full-universe scan
    (340+ curated large-cap symbols via one bulk yf.download() call) so the
    live dashboard request never has to wait on it. Without this, a cache
    miss fell back to Finnhub's per-symbol /quote calls, which can only check
    ~50 symbols within a reasonable timeout (60 req/min free tier, no bulk
    endpoint) — explaining why Top Gainers/Losers regularly showed far fewer
    than 10 names each for US.
    """
    await asyncio.sleep(120)  # let server settle first
    while True:
        try:
            loop = asyncio.get_event_loop()
            from services.screener_service import refresh_us_movers_cache
            await loop.run_in_executor(None, refresh_us_movers_cache)
        except Exception as e:
            log.warning(f"[us_movers_refresh] error: {e}")
        await asyncio.sleep(3 * 60)  # every 3 min — ahead of the 2-5 min movers cache TTL


async def _daily_picks_orphan_reconciliation_loop():
    """
    Periodic (in addition to the existing startup-only pass) orphan/restart
    recovery for Daily Picks jobs — added after the 2026-07-21 US incidents:
    a deployment orphaned one run mid phase_1, and its replacement was then
    killed (an OOM-consistent signature — no traceback, no shutdown log, a
    same-deployment-ID process restart) ~30-45s after entering the ranking
    phase. Both times, reconcile_stale_daily_picks_jobs()'s existing 6h-only
    startup pass never caught it: on the next boot, the row was only minutes
    stale, nowhere near 6h — so it would have sat 'running' for up to 6h
    regardless of how many more restarts happened before then.

    This loop closes that gap with a much shorter, periodic sweep using
    services.postgres_store._DAILY_PICKS_PERIODIC_STALE_INTERVAL (10
    minutes — see that constant's own comment for the full margin-over-
    heartbeat-cadence reasoning). It never touches a genuinely healthy job,
    which keeps writing a heartbeat every 30s no matter how long its run
    takes; it only reclaims a job whose owning process is provably gone.
    Like the startup pass, it never starts a replacement job itself — same
    manual-retry-required contract, just reached automatically and much
    sooner than 6 hours.
    """
    await asyncio.sleep(300)  # let server settle & the startup pass finish first
    log.info("[daily_picks_orphan_sweep] started")
    while True:
        try:
            from services.postgres_store import (
                reconcile_stale_daily_picks_jobs,
                _DAILY_PICKS_PERIODIC_STALE_INTERVAL,
            )
            loop = asyncio.get_running_loop()
            reclaimed = await loop.run_in_executor(
                None, reconcile_stale_daily_picks_jobs, _DAILY_PICKS_PERIODIC_STALE_INTERVAL,
            )
            if reclaimed:
                log.warning(f"[daily_picks_orphan_sweep] reconciled {reclaimed} stale Daily Picks job(s)")
        except Exception as e:
            log.warning(f"[daily_picks_orphan_sweep] sweep error: {e}")
        await asyncio.sleep(300)  # every 5 minutes


async def _price_alerts_check_loop():
    """
    Email backstop for the Alerts page (services/price_alert_notifier.py).
    The frontend only checks alerts client-side every 5s while the tab is
    open — close the tab, lock the phone, or let the browser discard a
    backgrounded tab and monitoring silently stops. This runs server-side on
    its own schedule so an alert still fires even then.

    Kill switch: set PRICE_ALERTS_ENFORCEMENT=0 in the environment to turn
    this off without a code change — checked every cycle, so flipping the
    var and letting Railway restart the service (which it already does on
    env var changes) is enough. The client-side polling on the Alerts page
    is unaffected either way.
    """
    await asyncio.sleep(100)  # let server settle first
    while True:
        if os.getenv("PRICE_ALERTS_ENFORCEMENT", "1") == "1":
            try:
                loop = asyncio.get_event_loop()
                from services.price_alert_notifier import check_and_notify
                await loop.run_in_executor(None, check_and_notify)
            except Exception as e:
                log.warning(f"[price_alerts] error: {e}")
        await asyncio.sleep(90)  # every 90s — far more responsive than email needs to be, still cheap


# V-SCHED1C2D — the allowlist and its parser now live in
# services/market_calendar.py, the single shared implementation both this
# scheduler and services.validation_engine's freshness classification
# call — never two independently maintained copies. This module-level
# alias keeps every existing call site (`_parse_auto_short_universes(...)`)
# and every existing test import (`from api.main import
# _parse_auto_short_universes`) working unchanged.
from services.market_calendar import parse_auto_short_universes as _parse_auto_short_universes  # noqa: E402


def enabled_validation_combinations() -> list[tuple[str, str]]:
    """The complete set of (horizon, universe) combinations the single
    weekly Saturday 12:00 UTC batch currently admits.

    2026-09 WEEKLY-ONLY POLICY (SES-006-governed, explicit user approval):
    short, medium AND long horizons now share the exact same weekly slot
    — this replaces the former independent daily short-horizon schedule
    (03:30 IST) entirely. Medium/long remain unconditionally enabled for
    all three universes (unchanged from before). Short is included ONLY
    for whichever universes VALIDATION_AUTO_SHORT_UNIVERSES currently
    enables — re-read from the environment on every call (never cached),
    so an ordinary redeploy/config change can enable or disable it
    without a code change, exactly as the old short scheduler did. This
    is the SINGLE place that decides "what does an admitted weekly batch
    contain" — the live scheduler and the startup missed-slot check both
    call this, never duplicating the enabled-universe logic."""
    enabled_short = _parse_auto_short_universes(os.getenv("VALIDATION_AUTO_SHORT_UNIVERSES"))
    combos: list[tuple[str, str]] = [("short", u) for u in enabled_short]
    combos += [(h, u) for h in ("medium", "long") for u in ("nifty100", "midcap", "us")]
    return combos


def compute_missed_validation_combinations(now_utc):
    """Pure(-ish — reads the durable ledger, never writes it), directly
    testable core of the startup missed-slot check. Returns
    (missed, this_weeks_slot, next_slot):
      - this_weeks_slot is None (missed always []) if `now_utc` is still
        before this week's Saturday 12:00 UTC window — there is nothing
        to have missed yet.
      - Otherwise, `missed` lists "horizon/universe" for every currently
        enabled combination whose slot for THIS week either doesn't exist
        yet or is still "due" — i.e. never reached a non-"due" status —
        EXCLUDING any combination that has never established a baseline
        at all (first deployment, not a missed run).
    Never creates a slot, never admits an attempt — read-only."""
    from services.market_calendar import last_saturday_1200_utc, next_saturday_1200_utc
    from services.validation_engine import find_schedule_slot, has_established_schedule_baseline

    if now_utc.tzinfo is None:
        raise ValueError("compute_missed_validation_combinations: now_utc must be timezone-aware")
    this_weeks_slot = last_saturday_1200_utc(now_utc)
    next_slot = next_saturday_1200_utc(now_utc)
    if now_utc < this_weeks_slot:
        return [], None, next_slot

    missed: list[str] = []
    for horizon, univ in enabled_validation_combinations():
        # V-SCHED1C1-ROLLOUT1 bootstrap safety, generalized: the very
        # first deployment of a given (horizon, universe) combination —
        # before its first-ever weekly tick has even happened — must
        # never be reported as "missed".
        if not has_established_schedule_baseline(horizon=horizon, universe=univ, schedule_version="v1"):
            continue
        slot = find_schedule_slot(horizon=horizon, universe=univ, scheduled_slot=this_weeks_slot, schedule_version="v1")
        if slot is None or slot["status"] == "due":
            missed.append(f"{horizon}/{univ}")
    return missed, this_weeks_slot, next_slot


async def _validation_schedule_loop():
    """
    Run walk-forward validation on a schedule, UTC-anchored:
      - Short, medium AND long horizons: every Saturday at 12:00 UTC
        (16:00 Dubai / 17:30 IST) — ONE weekly slot for all three.
    Sleeps until the next scheduled window, then fires in a thread pool
    so it never blocks the event loop.

    2026-09 WEEKLY-ONLY POLICY (SES-006-governed, explicit user approval):
    consolidates the PREVIOUS two independent schedules —
    (a) medium+long, already weekly (Saturday 12:00 UTC), and
    (b) short, previously an INDEPENDENT DAILY schedule at 03:30 IST
        (services/market_calendar.py's resolve_latest_completed_short_
        session, evaluated once per calendar day) —
    into this single weekly window covering all three horizons. The old
    per-day short scheduler (_short_validation_schedule_loop) and its
    startup catch-up (_short_catchup_validation) are REMOVED, not merely
    disabled — there is no code path left in this process that can start
    an automatic validation run outside this one weekly tick (the startup
    missed-slot check below is read-only/log-only; see
    _validation_missed_slot_check).

    Universes/enabled-set logic: see enabled_validation_combinations()
    above — the SINGLE shared definition of "what's in this week's
    batch", so the live scheduler and the missed-slot check can never
    silently drift apart on which combinations are expected.

    See services/market_calendar.py's next_saturday_1200_utc/
    last_saturday_1200_utc — the single shared implementation this loop,
    _validation_missed_slot_check, and the /status endpoint's displayed
    next-run all call, so there is exactly one definition of "the weekly
    slot", never independently maintained copies of the same
    day-of-week math.

    V-SCHED1C1 — each run still goes through
    services.validation_engine.execute_admitted_validation(), the single
    shared admission path (also used by the authenticated manual /run
    route) built on the V-SCHED1B durable ledger and global execution
    lease. A rejected/failed admission is logged distinctly from a
    genuine completion.

    Timing contract: Saturday 12:00 UTC is the scheduled BATCH START, not
    a requirement that every combination start simultaneously — each
    combination is awaited to completion before the next begins (never
    concurrently within this process), with an explicit 5-minute gap
    between them, exactly as before. A batch with (currently) up to
    3 short + 6 medium/long = 9 combinations can therefore continue for
    hours past the nominal 12:00 UTC instant — this is expected and
    matches the existing medium/long behavior, not a new risk.
    """
    from datetime import datetime, timezone, timedelta
    import uuid
    from services.market_calendar import next_saturday_1200_utc
    await asyncio.sleep(180)  # let server fully settle first
    log.info("[validation_scheduler] started")
    IST = timezone(timedelta(hours=5, minutes=30))
    DUBAI = timezone(timedelta(hours=4))
    owner = f"scheduler-{uuid.uuid4()}"

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            next_run = next_saturday_1200_utc(now_utc)  # UTC-aware; the sole source of truth for the trigger instant
            sleep_secs = (next_run - now_utc).total_seconds()
            log.info(
                f"[validation_scheduler] next weekly run at {next_run.isoformat()} "
                f"({next_run.astimezone(DUBAI).strftime('%a %H:%M')} Dubai / "
                f"{next_run.astimezone(IST).strftime('%a %H:%M')} IST) (in {sleep_secs/3600:.1f}h)"
            )
            await asyncio.sleep(sleep_secs)

            # All currently-enabled combinations share the single weekly
            # slot, staggered by 5 min between every horizon/universe pair
            # so nothing runs concurrently on the same admission owner —
            # relies entirely on the existing global lease/fencing
            # machinery in execute_admitted_validation(), not reimplemented.
            from services.validation_engine import execute_admitted_validation
            loop = asyncio.get_event_loop()
            slot_instant = next_run  # already UTC-aware
            for horizon, univ in enabled_validation_combinations():
                try:
                    log.info(f"[validation_scheduler] starting {horizon}/{univ} run…")
                    result = await loop.run_in_executor(
                        None,
                        lambda h=horizon, u=univ: execute_admitted_validation(
                            horizon=h, universe=u, trigger_type="scheduler", owner=owner,
                            scheduled_slot=slot_instant, schedule_version="v1",
                        ),
                    )
                    if result.get("ok"):
                        log.info(f"[validation_scheduler] {horizon}/{univ} complete (run_id={result.get('run_id')})")
                    else:
                        log.warning(f"[validation_scheduler] {horizon}/{univ} rejected/failed — reason={result.get('reason')}")
                except Exception as e:
                    log.warning(f"[validation_scheduler] {horizon}/{univ} error: {e}")
                await asyncio.sleep(5 * 60)  # 5-min gap between horizon/universe runs

        except Exception as e:
            log.warning(f"[validation_scheduler] scheduler error: {e}")
            await asyncio.sleep(3600)  # back off 1h on unexpected error


async def _warmup_loop():
    """
    Pre-warm 2 top-traffic stocks after startup so first user hit is a cache hit.
    Uses threading.Thread (same as the prediction endpoint) so tasks survive
    the asyncio lifecycle and never get cancelled by anyio.
    """
    await asyncio.sleep(90)  # wait for server to fully settle
    import threading, time
    from api.routers.predictions import engine, _computing, _bg_thread
    from services.prediction_engine import _pred_cache, _PRED_TTL
    # Top-traffic stocks across both markets — pre-warm so first user hit is a cache hit
    warmup = [
        ("RELIANCE", "IN", "medium"), ("TCS",      "IN", "medium"),
        ("HDFCBANK", "IN", "medium"), ("INFY",     "IN", "medium"),
        ("AAPL",     "US", "medium"), ("MSFT",     "US", "medium"),
    ]
    log.info(f"[warmup] Pre-warming {len(warmup)} stocks…")
    for sym, mkt, horizon in warmup:
        key = f"{sym}:{mkt}:{horizon}"
        if (_pred_cache.get(key) and (time.time() - _pred_cache[key][0]) < _PRED_TTL) or key in _computing:
            continue
        _computing.add(key)
        t = threading.Thread(target=_bg_thread, args=(sym, mkt, horizon, key), daemon=True)
        t.start()
        log.info(f"[warmup] kicked off {key}")
        await asyncio.sleep(45)  # stagger launches — don't hammer Yahoo all at once
    log.info("[warmup] Pre-warm triggered.")


def startup_catchup_enabled() -> bool:
    """Return True only when DAILY_PICKS_STARTUP_CATCHUP_ENABLED is 1/true/yes.
    Reads env at call time so tests can patch os.environ deterministically."""
    value = os.getenv("DAILY_PICKS_STARTUP_CATCHUP_ENABLED", "0")
    return value.strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import init_db
            init_db()
            log.info("[startup] Postgres schema initialized")
        except Exception as e:
            # Schema Initialization Hardening phase — deliberately NOT
            # log-and-continue. init_db() itself is now fail-closed and
            # concurrency-safe (session-level advisory lock, bounded
            # retry only for approved transient SQLSTATEs, metadata-only
            # postcondition verification); an exception reaching this
            # point means initialization could not be safely completed
            # even after retrying. Re-raising here prevents FastAPI
            # startup from completing at all, so /health can never report
            # ready against unverified schema state — never DATABASE_URL,
            # hostname, credentials, raw SQL or row content, only the
            # sanitized exception class/SQLSTATE.
            sqlstate = getattr(e, "sqlstate", None) or getattr(getattr(e, "__cause__", None), "sqlstate", None)
            log.critical(
                f"[startup] Postgres schema initialization failed terminally "
                f"(sqlstate={sqlstate} {type(e).__name__}) — refusing to start with unverified schema"
            )
            raise
        try:
            from services.validation_engine import init_db as init_validation_db
            init_validation_db()
            log.info("[startup] Validation schema initialized")
        except Exception as e:
            log.warning(f"[startup] Validation schema init failed: {e}")
        # Product Integrity #009 §9 — orphan/restart recovery for Multibagger
        # weekly jobs. Startup-only, one pass: a genuinely active job's
        # heartbeat is recent and is never touched; only a row silent for
        # longer than a credible individual-symbol operation is reclassified
        # 'interrupted'. Never invoked from a request path (GET /status must
        # not mutate lifecycle state).
        try:
            from services.postgres_store import reconcile_stale_multibagger_jobs
            reclaimed = reconcile_stale_multibagger_jobs()
            if reclaimed:
                log.warning(f"[startup] Reconciled {reclaimed} stale Multibagger job(s) to 'interrupted'")
        except Exception as e:
            log.warning(f"[startup] Multibagger stale-job reconciliation failed: {e}")
        # 2026-07-17 — same orphan/restart recovery as Multibagger above,
        # for Daily Picks jobs (previously manual-only; see
        # reconcile_stale_daily_picks_jobs()'s docstring for the incident
        # that prompted this). Startup-only, one pass; never invoked from a
        # request path.
        try:
            from services.postgres_store import reconcile_stale_daily_picks_jobs
            reclaimed_picks = reconcile_stale_daily_picks_jobs()
            if reclaimed_picks:
                log.warning(f"[startup] Reconciled {reclaimed_picks} stale Daily Picks job(s) to 'interrupted'")
        except Exception as e:
            log.warning(f"[startup] Daily Picks stale-job reconciliation failed: {e}")
    # Force yfinance crumb refresh so cloud IP starts with a valid session
    try:
        import yfinance as yf
        loop = asyncio.get_running_loop()
        def _refresh_crumb():
            try:
                if hasattr(yf.utils, "get_crumb"):
                    yf.utils.get_crumb(force=True)
                yf.Ticker("AAPL").fast_info
                yf.Ticker("RELIANCE.NS").fast_info   # warm Indian session too
                log.info("[startup] yfinance session initialised")
            except Exception as e:
                log.warning(f"[startup] yfinance crumb refresh failed (non-fatal): {e}")
        await asyncio.wait_for(loop.run_in_executor(None, _refresh_crumb), timeout=15.0)
    except asyncio.TimeoutError:
        log.warning("[startup] yfinance init timed out after 15s — continuing without pre-warm")
    # Warm NSE session (non-blocking — homepage may return 403 on Render, that's ok)
    try:
        from services import nse_client
        loop = asyncio.get_running_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, nse_client._ensure_session), timeout=10.0
        )
    except Exception:
        pass
    except Exception as e:
        log.warning(f"[startup] yfinance init error: {e}")

    # Pre-login to screener.in so first stock request is already authenticated
    try:
        from services.screener_data import _login
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(loop.run_in_executor(None, _login), timeout=15.0)
        log.warning(f"[startup] screener.in login {'succeeded' if result else 'failed (check SCREENER_EMAIL/SCREENER_PASSWORD)'}")
    except asyncio.TimeoutError:
        log.warning("[startup] screener.in login timed out after 15s — will retry on first request")
    except Exception as e:
        log.warning(f"[startup] screener.in login error: {e}")

    # Pre-warm movers cache so dashboard is never blank on first load
    try:
        from services.screener_service import _closed_gainers_losers, _IN_FALLBACK_UNIVERSE, _movers_cache, _last_good_movers, _is_market_open
        from services import screener_service as _ss

        def _warmup_movers():
            try:
                g, l = _closed_gainers_losers(_IN_FALLBACK_UNIVERSE)
                if g or l:
                    is_open = _is_market_open("IN")
                    resp = {"market": "IN", "market_open": is_open,
                            "gainers": g, "losers": l, "movers": g + l, "error": None}
                    import time as _t
                    _movers_cache["IN"] = (_t.time(), resp)
                    _last_good_movers["IN"] = resp
                    log.info(f"[startup] movers pre-warm: {len(g)} gainers, {len(l)} losers")
                else:
                    log.info("[startup] movers pre-warm: no data returned")
            except Exception as e:
                log.warning(f"[startup] movers pre-warm error: {e}")

        await asyncio.wait_for(loop.run_in_executor(None, _warmup_movers), timeout=35.0)
    except Exception as e:
        log.warning(f"[startup] movers pre-warm failed: {e}")

    # Catch-up picks: if server restarted after a market's scheduled generation
    # time on a market day and today's picks haven't been generated, run them
    # now. This recovers from redeploys that killed a mid-run background task,
    # and from GitHub Actions PICKS_SECRET mismatches. Same recovery logic for
    # both markets — only the trigger-time/timezone/weekday rule differs.
    async def _catchup_picks(market: str, tz, trigger_hour: int, settle_secs: int):
        if not startup_catchup_enabled():
            log.info(f"[picks_catchup] [{market}] Startup catch-up disabled by configuration — skipping.")
            return
        import uuid as _uuid
        from datetime import datetime
        await asyncio.sleep(settle_secs)  # let server settle first
        try:
            now = datetime.now(tz)
            trigger_time = now.replace(hour=trigger_hour, minute=0, second=0, microsecond=0)
            if now < trigger_time:
                log.info(f"[picks_catchup] [{market}] Before {trigger_hour:02d}:00 local — skipping")
                return
            if now.weekday() >= 5:
                log.info(f"[picks_catchup] [{market}] Weekend — skipping picks catchup")
                return
            from services.daily_picks import picks_generated_today, generate_picks
            import services.daily_picks as _dp
            if picks_generated_today(market):
                log.info(f"[picks_catchup] [{market}] Today's picks already exist — skipping")
                return

            # Durable Postgres state is mandatory — no legacy in-memory fallback
            if os.getenv("USE_POSTGRES") != "1":
                log.warning(
                    f"[picks_catchup] [{market}] durable_job_state_unavailable: "
                    f"USE_POSTGRES is not enabled — skipping catch-up"
                )
                return

            job_id = None

            # Durable reservation gate — same mutual-exclusion path as POST /generate
            with _dp._generating_lock:
                if _dp._generating.get(market, False):
                    log.info(f"[picks_catchup] [{market}] Generation already in progress — skipping")
                    return

            job_id = str(_uuid.uuid4())
            try:
                from services.postgres_store import try_reserve_daily_picks_job
                reserved = try_reserve_daily_picks_job(job_id, market, _dp._RUNNER_ID)
            except Exception as exc:
                log.warning(f"[picks_catchup] [{market}] DB reservation failed: {exc} — skipping")
                return

            if not reserved:
                log.info(f"[picks_catchup] [{market}] Job already reserved by another process — skipping")
                return

            with _dp._generating_lock:
                _dp._generating[market] = True

            log.info(f"[picks_catchup] [{market}] No picks for today — generating now (this takes ~10-20 min)…")
            try:
                loop2 = asyncio.get_running_loop()
                await loop2.run_in_executor(None, generate_picks, market, job_id)
                log.info(f"[picks_catchup] [{market}] picks generation complete")
            finally:
                _dp._generating[market] = False
        except Exception as e:
            log.warning(f"[picks_catchup] [{market}] error: {e}")
            import services.daily_picks as _dp2
            _dp2._generating[market] = False

    # 2026-09 WEEKLY-ONLY POLICY (SES-006-governed, explicit user
    # approval): startup missed-slot check — READ-ONLY, NEVER EXECUTES
    # VALIDATION. This deliberately REPLACES the previous
    # _catchup_validation/_short_catchup_validation functions, which used
    # to admit and RUN a missed slot's validation directly from startup.
    # That behavior is an "unscheduled startup backfill" by definition —
    # exactly what the current policy prohibits. The only code path in
    # this process that may ever start an automatic validation run is
    # _validation_schedule_loop's own weekly tick, above.
    #
    # Lateness tolerance for automatic EXECUTION is therefore explicitly
    # ZERO: if this week's Saturday 12:00 UTC slot has passed without
    # every currently-enabled combination reaching a non-"due" status,
    # this function logs exactly which combinations were missed and when
    # the next scheduled slot is — it never creates a slot, never admits
    # an attempt, never calls execute_admitted_validation. The lease and
    # ledger machinery's own stale-lease recovery / terminal-status
    # reconciliation (inside admit_validation_attempt, unchanged) remains
    # fully available to the NEXT scheduled Saturday tick if a prior
    # attempt crashed mid-run — this function does not touch or bypass
    # that; it is purely an observability log line.
    async def _validation_missed_slot_check():
        from datetime import datetime, timezone
        await asyncio.sleep(300)  # wait 5 min for server to fully settle
        try:
            now_utc = datetime.now(timezone.utc)
            missed, this_weeks_slot, next_slot = compute_missed_validation_combinations(now_utc)
            if this_weeks_slot is None:
                return  # before this week's scheduled window — nothing to have missed yet
            if missed:
                log.warning(
                    f"[validation_scheduler] missed this week's Saturday 12:00 UTC slot "
                    f"({this_weeks_slot.isoformat()}) for: {', '.join(missed)} — recording as missed, "
                    f"no off-schedule backfill will be started. Next scheduled slot: {next_slot.isoformat()}."
                )
            else:
                log.info(
                    f"[validation_scheduler] this week's Saturday 12:00 UTC slot "
                    f"({this_weeks_slot.isoformat()}) has no missed combinations. "
                    f"Next scheduled slot: {next_slot.isoformat()}."
                )
        except Exception as e:
            log.warning(f"[validation_scheduler] missed-slot check error: {e}")

    task = asyncio.create_task(_weekly_refresh_loop())
    keepalive = asyncio.create_task(_keepalive_loop())
    outcome_task = asyncio.create_task(_outcome_resolver_loop())
    warmup_task = asyncio.create_task(_warmup_loop())
    crumb_task = asyncio.create_task(_yfinance_crumb_loop())
    validation_task = asyncio.create_task(_validation_schedule_loop())
    missed_slot_check_task = asyncio.create_task(_validation_missed_slot_check())
    from zoneinfo import ZoneInfo
    from datetime import timezone as _tz, timedelta as _td
    _IST = _tz(_td(hours=5, minutes=30))
    _ET = ZoneInfo("America/New_York")  # DST-aware, matches services/market_hours.py
    # IN: catch up any time after 2 AM IST on a weekday. trigger_hour has
    # only hour-level granularity; the actual IN cron fires at 2:07 AM IST
    # (.github/workflows/daily_picks_in.yml), ~7 min after this threshold —
    # close enough that a restart in that narrow window simply lets the
    # atomic job-reservation path (try_reserve_daily_picks_job) decide which
    # of catch-up-vs-cron wins, rather than needing minute-level precision
    # here. Previously drifted stale for ~1 week (2026-07-09 to 2026-07-16)
    # when the cron moved to 3:26 AM IST without this threshold following —
    # see Product Integrity #013.
    # US: base-generation cron fires at 06:00 UTC (2 AM EDT / 1 AM EST).
    # trigger_hour=3 (3 AM ET) is safely after both DST local times of that
    # run and leaves runway before the 6 AM ET Premarket Finalizer target.
    if startup_catchup_enabled():
        picks_catchup_task = asyncio.create_task(_catchup_picks("IN", _IST, 2, 60))
        # 2026-07-15: US threshold moved from 9 AM ET to 3 AM ET (Product
        # Integrity #008) — 9 AM ET was later than the Premarket Finalizer's
        # 7:30 AM ET cutoff, so a restart between 7:30 and 9 AM ET would have
        # let catch-up silently miss the finalizer window for that day even
        # after generating a valid base. 3 AM ET is safely after both DST
        # local times of the 06:00 UTC base run (2 AM EDT / 1 AM EST) and
        # leaves hours of runway before the 6 AM ET finalizer target.
        picks_catchup_task_us = asyncio.create_task(_catchup_picks("US", _ET, 3, 90))
    else:
        log.info("[startup] Daily Picks startup catch-up disabled by configuration.")
        async def _no_catchup(): pass
        picks_catchup_task = asyncio.create_task(_no_catchup())
        picks_catchup_task_us = asyncio.create_task(_no_catchup())
    trade_notify_task = asyncio.create_task(_paper_trade_notify_loop())
    trade_exit_monitor_task = asyncio.create_task(_paper_trade_exit_monitor_loop())
    us_movers_task = asyncio.create_task(_us_movers_refresh_loop())
    price_alerts_task = asyncio.create_task(_price_alerts_check_loop())
    daily_picks_orphan_sweep_task = asyncio.create_task(_daily_picks_orphan_reconciliation_loop())

    # Wave B, Stage J4F — bounded background postmortem outbox worker.
    # Flag-gated behind the existing TRADE_POSTMORTEM_PRICE_PATH_ENABLED
    # flag (no new flag activation this wave), default-disabled.
    # Best-effort infrastructure: a startup failure here is caught and
    # logged, never allowed to prevent the rest of the application from
    # starting.
    postmortem_worker_task = None
    if os.getenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from services.market_hours import ET as _PM_ET, IST as _PM_IST
            from services.postmortem.outbox_worker import start_outbox_worker
            from api.routers.paper_trading import _conn as _postmortem_conn
            postmortem_worker_task = start_outbox_worker(
                _postmortem_conn,
                market_tzinfo_by_market={
                    "IN": (_PM_IST, "Asia/Kolkata"),
                    "US": (_PM_ET, "America/New_York"),
                },
            )
            log.info("[startup] postmortem outbox worker started")
        except Exception:
            log.warning("[startup] postmortem outbox worker failed to start — continuing without it")

    yield
    task.cancel()
    keepalive.cancel()
    outcome_task.cancel()
    warmup_task.cancel()
    crumb_task.cancel()
    validation_task.cancel()
    missed_slot_check_task.cancel()
    picks_catchup_task.cancel()
    picks_catchup_task_us.cancel()
    trade_notify_task.cancel()
    trade_exit_monitor_task.cancel()
    us_movers_task.cancel()
    price_alerts_task.cancel()
    daily_picks_orphan_sweep_task.cancel()
    for t in (task, keepalive, outcome_task, warmup_task, crumb_task, validation_task, missed_slot_check_task,
              picks_catchup_task, picks_catchup_task_us, trade_notify_task, trade_exit_monitor_task,
              us_movers_task, price_alerts_task, daily_picks_orphan_sweep_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    if postmortem_worker_task is not None:
        try:
            from services.postmortem.outbox_worker import stop_outbox_worker
            await stop_outbox_worker()
        except Exception:
            log.warning("[shutdown] postmortem outbox worker stop failed")


app = FastAPI(
    title="StockSense360 API",
    description="AI-powered stock prediction for US & India markets",
    version="1.0.0",
    lifespan=lifespan,
)

# Local development exception: only plain http(s)://localhost:3000 is
# allowed unconditionally. Every other origin must be the project's own,
# explicitly-configured production/staging frontend — never a shared
# wildcard domain. (Security Remediation Sprint #001, H-1: the previous
# `allow_origin_regex=r"https://.*\.vercel\.app"` matched ANY app hosted
# on Vercel's shared domain, not just this project's own deployment; combined
# with allow_credentials=True that was a real cross-tenant risk, especially
# once real bearer-token auth — added in this same sprint — exists for an
# attacker-controlled *.vercel.app page to send.)
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://localhost:3000",
]
frontend_url = os.getenv("FRONTEND_URL", "")
if frontend_url:
    ALLOWED_ORIGINS.append(frontend_url)
staging_frontend_url = os.getenv("STAGING_FRONTEND_URL", "")
if staging_frontend_url:
    ALLOWED_ORIGINS.append(staging_frontend_url)

# Optional, project-scoped Vercel preview-deployment pattern (e.g.
# r"https://stocksense360-[a-z0-9-]+\.vercel\.app" for this project's own
# preview URLs only) — unset by default. Never set this to a bare
# `.*\.vercel\.app` pattern; that reintroduces the exact H-1 finding.
preview_origin_regex = os.getenv("VERCEL_PREVIEW_ORIGIN_REGEX", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=preview_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _sanitize_non_finite_json(value):
    """Recursively replaces a non-finite float (NaN/Infinity/-Infinity)
    with a safe string placeholder — everything else passes through
    unchanged. Used only to make validation-error bodies JSON-safe; never
    called anywhere near a successful response or persistence path."""
    if isinstance(value, float) and not math.isfinite(value):
        return "<rejected: non-finite number>"
    if isinstance(value, dict):
        return {k: _sanitize_non_finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_non_finite_json(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _stable_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Stage 2 migration-verification gate — fixes a real, reachable path to
    an unhandled 500. FastAPI's default RequestValidationError handler
    echoes each rejected field's raw `input` value back in the error body.
    Starlette's JSONResponse renders with `json.dumps(..., allow_nan=False)`
    (strict, spec-compliant JSON) — which raises `ValueError` (an unhandled
    500, not a clean 4xx) if that echoed input is NaN/Infinity/-Infinity.

    This is trivially reachable: Python's own `json.loads` accepts bare
    `NaN`/`Infinity`/`-Infinity` tokens as a well-known non-standard
    extension by default, so any client (not just a browser bound by
    JSON.stringify's stricter behavior) can send a request body containing
    one to ANY endpoint with a Pydantic `float` field — not specific to
    paper trading or Stage 2.

    Narrowly scoped to `RequestValidationError` only (the single, specific
    exception FastAPI itself raises for body/query/path validation
    failures) — this does not catch, hide, or alter behavior for any other
    exception type; an unrelated bug elsewhere still propagates and 500s
    exactly as before. `jsonable_encoder` first reproduces FastAPI's normal
    error-body shape (including its usual ctx/exception-to-string handling)
    so ordinary validation errors are byte-for-byte unchanged; only a
    non-finite float's `input` value is ever replaced.
    """
    sanitized = _sanitize_non_finite_json(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": sanitized})

app.include_router(stocks.router,      prefix="/api/stocks",      tags=["Stocks"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["Predictions"])
app.include_router(news.router,        prefix="/api/news",        tags=["News & Sentiment"])
app.include_router(screener.router,    prefix="/api/screener",    tags=["Screener"])
app.include_router(watchlist.router,   prefix="/api/watchlist",   tags=["Watchlist"])
app.include_router(backtest.router,    prefix="/api/backtest",    tags=["Backtest"])
app.include_router(picks.router,       prefix="/api/picks",       tags=["Daily Picks"])
app.include_router(validation.router,     prefix="/api/validation",     tags=["Model Validation"])
app.include_router(paper_trading.router,  prefix="/api/paper-trading",  tags=["Paper Trading"])
app.include_router(alerts.router,         tags=["Alerts"])
app.include_router(auth.router,           tags=["Auth"])
app.include_router(feedback.router)
app.include_router(portfolio.router)
app.include_router(multibagger.router)
app.include_router(leadership.router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
