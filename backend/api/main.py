import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import stocks, predictions, news, screener, watchlist

app = FastAPI(
    title="StockSense API",
    description="AI-powered stock prediction for US & India markets",
    version="1.0.0",
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

app.include_router(stocks.router, prefix="/api/stocks", tags=["Stocks"])
app.include_router(predictions.router, prefix="/api/predictions", tags=["Predictions"])
app.include_router(news.router, prefix="/api/news", tags=["News & Sentiment"])
app.include_router(screener.router, prefix="/api/screener", tags=["Screener"])
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["Watchlist"])


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}
