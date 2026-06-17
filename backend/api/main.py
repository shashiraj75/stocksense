import os
import sys
import asyncio
import importlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import stocks, predictions, news, screener, watchlist, backtest, picks, validation

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("USE_POSTGRES") == "1":
        try:
            from services.postgres_store import init_db
            init_db()
            print("[startup] Postgres schema initialized")
        except Exception as e:
            print(f"[startup] Postgres init failed: {e}")
    task = asyncio.create_task(_weekly_refresh_loop())
    keepalive = asyncio.create_task(_keepalive_loop())
    yield
    task.cancel()
    keepalive.cancel()
    for t in (task, keepalive):
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
app.include_router(validation.router,  prefix="/api/validation",  tags=["Model Validation"])


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
