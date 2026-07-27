"""Shared synthetic price-series fixtures for market_leadership unit tests.

All series are deterministic (fixed seed / closed-form paths) — no randomness
across test runs, matching the property that identical inputs must produce
identical outputs.
"""
import numpy as np
import pandas as pd
import pytest


def make_price_df(n_sessions: int, start_price: float = 100.0,
                  daily_drift_pct: float = 0.0, volatility_pct: float = 0.0,
                  seed: int = 42, start_date: str = "2024-01-01",
                  volume: float = 1_000_000.0) -> pd.DataFrame:
    """A deterministic OHLCV DataFrame with a given drift/volatility, tz-aware
    business-day index (mirrors yfinance's own index shape)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start_date, periods=n_sessions, tz="UTC")
    noise = rng.normal(0, volatility_pct / 100.0, n_sessions) if volatility_pct else np.zeros(n_sessions)
    rets = np.full(n_sessions, daily_drift_pct / 100.0) + noise
    closes = start_price * np.cumprod(1 + rets)
    highs = closes * 1.01
    lows = closes * 0.99
    opens = np.roll(closes, 1)
    opens[0] = start_price
    volumes = np.full(n_sessions, volume)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def make_uptrend_df(n_sessions: int = 300, **kw) -> pd.DataFrame:
    return make_price_df(n_sessions, daily_drift_pct=0.15, volatility_pct=0.5, **kw)


def make_downtrend_df(n_sessions: int = 300, **kw) -> pd.DataFrame:
    return make_price_df(n_sessions, daily_drift_pct=-0.15, volatility_pct=0.5, **kw)


def make_flat_df(n_sessions: int = 300, **kw) -> pd.DataFrame:
    return make_price_df(n_sessions, daily_drift_pct=0.0, volatility_pct=0.1, **kw)


@pytest.fixture
def uptrend_df():
    return make_uptrend_df()


@pytest.fixture
def downtrend_df():
    return make_downtrend_df()


@pytest.fixture
def flat_df():
    return make_flat_df()


@pytest.fixture
def benchmark_flat_df():
    return make_price_df(300, daily_drift_pct=0.02, volatility_pct=0.3, seed=7)
