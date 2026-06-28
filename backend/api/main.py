import os
import sys
import asyncio
import importlib
import logging
from contextlib import asynccontextmanager

from services.logging_config import configure_logging
configure_logging()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from api.routers import stocks, predictions, news, screener, watchlist, backtest, picks, validation, paper_trading, alerts, auth, feedback, portfolio, multibagger
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


async def _validation_schedule_loop():
    """
    Run walk-forward validation on a schedule (IST = UTC+5:30):
      - Medium horizon: daily at 06:00 IST (00:30 UTC)
      - Long horizon:   every Sunday at 06:00 IST (00:30 UTC)
    Sleeps until the next scheduled window, then fires in a thread pool
    so it never blocks the event loop.
    """
    from datetime import datetime, timezone, timedelta
    await asyncio.sleep(180)  # let server fully settle first
    log.info("[validation_scheduler] started")
    IST = timezone(timedelta(hours=5, minutes=30))
    TARGET_HOUR = 6  # 6:00 AM IST

    while True:
        try:
            now_ist = datetime.now(IST)
            # Next 06:00 IST
            next_run = now_ist.replace(hour=TARGET_HOUR, minute=0, second=0, microsecond=0)
            if now_ist >= next_run:
                next_run += timedelta(days=1)
            sleep_secs = (next_run - now_ist).total_seconds()
            log.info(f"[validation_scheduler] next medium run at {next_run.isoformat()} IST (in {sleep_secs/3600:.1f}h)")
            await asyncio.sleep(sleep_secs)

            # Run medium validation for all three universes — staggered by 5 min each
            from services.validation_engine import run_validation
            loop = asyncio.get_event_loop()
            for univ in ("nifty100", "midcap", "us"):
                try:
                    log.info(f"[validation_scheduler] starting medium/{univ} run…")
                    await loop.run_in_executor(None, lambda u=univ: run_validation(horizon="medium", universe=u))
                    log.info(f"[validation_scheduler] medium/{univ} complete")
                except Exception as e:
                    log.warning(f"[validation_scheduler] medium/{univ} error: {e}")
                await asyncio.sleep(5 * 60)  # 5-min gap between universe runs

            # Run long only on Sundays (weekday 6) — all three universes
            if datetime.now(IST).weekday() == 6:
                for univ in ("nifty100", "midcap", "us"):
                    try:
                        log.info(f"[validation_scheduler] Sunday — starting long/{univ} run…")
                        await loop.run_in_executor(None, lambda u=univ: run_validation(horizon="long", universe=u))
                        log.info(f"[validation_scheduler] long/{univ} complete")
                    except Exception as e:
                        log.warning(f"[validation_scheduler] long/{univ} error: {e}")
                    await asyncio.sleep(5 * 60)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import init_db
            init_db()
            log.info("[startup] Postgres schema initialized")
        except Exception as e:
            log.warning(f"[startup] Postgres init failed: {e}")
        try:
            from services.validation_engine import init_db as init_validation_db
            init_validation_db()
            log.info("[startup] Validation schema initialized")
        except Exception as e:
            log.warning(f"[startup] Validation schema init failed: {e}")
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
            # Use the same lock as POST /api/picks/generate so a cron-triggered
            # run and this startup catch-up can't both pass the check and run
            # concurrently — this is the exact TOCTOU race the lock exists to
            # close (see _generating_lock comment in daily_picks.py).
            with _dp._generating_lock:
                if _dp._generating.get(market, False):
                    log.info(f"[picks_catchup] [{market}] Generation already in progress — skipping")
                    return
                _dp._generating[market] = True
            log.info(f"[picks_catchup] [{market}] No picks for today — generating now (this takes ~10-20 min)…")
            try:
                loop2 = asyncio.get_running_loop()
                await loop2.run_in_executor(None, generate_picks, market)
                log.info(f"[picks_catchup] [{market}] picks generation complete")
            finally:
                _dp._generating[market] = False
        except Exception as e:
            log.warning(f"[picks_catchup] [{market}] error: {e}")
            import services.daily_picks as _dp2
            _dp2._generating[market] = False

    # Catch-up validation: if server restarted after 6 AM IST and today's run
    # was missed (e.g. due to deployment), fire it in the background.
    async def _catchup_validation():
        from datetime import datetime, timezone, timedelta
        await asyncio.sleep(300)  # wait 5 min for server to fully settle
        try:
            IST = timezone(timedelta(hours=5, minutes=30))
            now = datetime.now(IST)
            today_6am = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now < today_6am:
                return  # before today's scheduled window — nothing to catch up
            # Check when the last medium run was
            from services.validation_engine import get_last_run_time
            last_run = get_last_run_time("medium")
            if last_run and last_run >= today_6am:
                log.info("[catchup] validation already ran today — skipping")
                return
            from services.validation_engine import run_validation
            log.info("[catchup] missed today's 6 AM validation — running now…")
            loop2 = asyncio.get_running_loop()
            await loop2.run_in_executor(None, lambda: run_validation(horizon="medium"))
            log.info("[catchup] catch-up validation complete")
        except Exception as e:
            log.warning(f"[catchup] validation catch-up error: {e}")

    task = asyncio.create_task(_weekly_refresh_loop())
    keepalive = asyncio.create_task(_keepalive_loop())
    outcome_task = asyncio.create_task(_outcome_resolver_loop())
    warmup_task = asyncio.create_task(_warmup_loop())
    crumb_task = asyncio.create_task(_yfinance_crumb_loop())
    validation_task = asyncio.create_task(_validation_schedule_loop())
    catchup_task = asyncio.create_task(_catchup_validation())
    from zoneinfo import ZoneInfo
    from datetime import timezone as _tz, timedelta as _td
    _IST = _tz(_td(hours=5, minutes=30))
    _ET = ZoneInfo("America/New_York")  # DST-aware, matches services/market_hours.py
    # IN: catch up any time after 2 AM IST (the scheduled run time) on a weekday.
    picks_catchup_task = asyncio.create_task(_catchup_picks("IN", _IST, 2, 60))
    # US: cron fires ~12:30 UTC (8:30 AM ET / 7:30 AM ET depending on DST).
    # trigger_hour=9 leaves margin either way before declaring the run missed.
    picks_catchup_task_us = asyncio.create_task(_catchup_picks("US", _ET, 9, 90))
    trade_notify_task = asyncio.create_task(_paper_trade_notify_loop())
    us_movers_task = asyncio.create_task(_us_movers_refresh_loop())
    price_alerts_task = asyncio.create_task(_price_alerts_check_loop())
    yield
    task.cancel()
    keepalive.cancel()
    outcome_task.cancel()
    warmup_task.cancel()
    crumb_task.cancel()
    validation_task.cancel()
    catchup_task.cancel()
    picks_catchup_task.cancel()
    picks_catchup_task_us.cancel()
    trade_notify_task.cancel()
    us_movers_task.cancel()
    price_alerts_task.cancel()
    for t in (task, keepalive, outcome_task, warmup_task, crumb_task, validation_task, catchup_task,
              picks_catchup_task, picks_catchup_task_us, trade_notify_task, us_movers_task, price_alerts_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


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


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
