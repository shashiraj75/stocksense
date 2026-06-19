import os
import sys
import asyncio
import importlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import stocks, predictions, news, screener, watchlist, backtest, picks, validation, paper_trading, alerts

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
        print(f"[universe] Background refresh error: {e}")


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
            print("[universe] Reload complete — search list is up to date.")
    except Exception as e:
        print(f"[universe] Refresh failed (existing list still active): {e}")


async def _weekly_refresh_loop():
    """Background task: refresh once on startup, then every 7 days."""
    await asyncio.sleep(30)          # let server fully start first
    while True:
        print("[universe] Starting scheduled refresh …")
        await _refresh_universe()
        print(f"[universe] Next refresh in {REFRESH_INTERVAL_SECONDS // 3600}h.")
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def _keepalive_loop():
    """
    Ping own /health every 14 minutes as a secondary keepalive fallback.
    Uses asyncio-native HTTP so it never blocks the event loop.
    Primary keepalive is UptimeRobot pinging /health every 5 min from outside.
    """
    await asyncio.sleep(60)
    self_url = os.getenv("RENDER_EXTERNAL_URL", "")
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
            print(f"[keepalive] pinged {url}")
        except Exception as e:
            print(f"[keepalive] ping failed: {e}")
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
            print("[crumb] yfinance session refreshed")
        except Exception as e:
            print(f"[crumb] refresh failed (non-fatal): {e}")
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
            print(f"[outcome_resolver] error: {e}")
        await asyncio.sleep(6 * 3600)


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
    print("[validation_scheduler] started")
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
            print(f"[validation_scheduler] next medium run at {next_run.isoformat()} IST (in {sleep_secs/3600:.1f}h)")
            await asyncio.sleep(sleep_secs)

            # Run medium every day
            try:
                loop = asyncio.get_event_loop()
                from services.validation_engine import run_validation
                print("[validation_scheduler] starting medium horizon run…")
                await loop.run_in_executor(None, lambda: run_validation(horizon="medium"))
                print("[validation_scheduler] medium run complete")
            except Exception as e:
                print(f"[validation_scheduler] medium run error: {e}")

            # Run long only on Sundays (weekday 6)
            if datetime.now(IST).weekday() == 6:
                try:
                    loop = asyncio.get_event_loop()
                    print("[validation_scheduler] Sunday — starting long horizon run…")
                    await loop.run_in_executor(None, lambda: run_validation(horizon="long"))
                    print("[validation_scheduler] long run complete")
                except Exception as e:
                    print(f"[validation_scheduler] long run error: {e}")

        except Exception as e:
            print(f"[validation_scheduler] scheduler error: {e}")
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
    print(f"[warmup] Pre-warming {len(warmup)} stocks…")
    for sym, mkt, horizon in warmup:
        key = f"{sym}:{mkt}:{horizon}"
        if (_pred_cache.get(key) and (time.time() - _pred_cache[key][0]) < _PRED_TTL) or key in _computing:
            continue
        _computing.add(key)
        t = threading.Thread(target=_bg_thread, args=(sym, mkt, horizon, key), daemon=True)
        t.start()
        print(f"[warmup] kicked off {key}")
        await asyncio.sleep(45)  # stagger launches — don't hammer Yahoo all at once
    print("[warmup] Pre-warm triggered.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import init_db
            init_db()
            print("[startup] Postgres schema initialized")
        except Exception as e:
            print(f"[startup] Postgres init failed: {e}")
    # Force yfinance crumb refresh so cloud IP starts with a valid session
    try:
        import yfinance as yf
        loop = asyncio.get_running_loop()
        def _refresh_crumb():
            try:
                if hasattr(yf.utils, "get_crumb"):
                    yf.utils.get_crumb(force=True)
                # Warm a lightweight ticker to establish session cookies
                yf.Ticker("AAPL").fast_info
                print("[startup] yfinance session initialised")
            except Exception as e:
                print(f"[startup] yfinance crumb refresh failed (non-fatal): {e}")
        await loop.run_in_executor(None, _refresh_crumb)
    except Exception as e:
        print(f"[startup] yfinance init error: {e}")

    # Pre-login to screener.in so first stock request is already authenticated
    try:
        from services.screener_data import _login
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _login)
        print(f"[startup] screener.in login {'succeeded' if result else 'failed (check SCREENER_EMAIL/SCREENER_PASSWORD)'}")
    except Exception as e:
        print(f"[startup] screener.in login error: {e}")

    task = asyncio.create_task(_weekly_refresh_loop())
    keepalive = asyncio.create_task(_keepalive_loop())
    outcome_task = asyncio.create_task(_outcome_resolver_loop())
    warmup_task = asyncio.create_task(_warmup_loop())
    crumb_task = asyncio.create_task(_yfinance_crumb_loop())
    validation_task = asyncio.create_task(_validation_schedule_loop())
    yield
    task.cancel()
    keepalive.cancel()
    outcome_task.cancel()
    warmup_task.cancel()
    crumb_task.cancel()
    validation_task.cancel()
    for t in (task, keepalive, outcome_task, warmup_task, crumb_task, validation_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="StockSense API",
    description="AI-powered stock prediction for US & India markets",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow localhost in dev + any Vercel deployment URL in production
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://localhost:3000",
]
frontend_url = os.getenv("FRONTEND_URL", "")
if frontend_url:
    ALLOWED_ORIGINS.append(frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
