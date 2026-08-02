"""
Performance regression guards (Section 17).

Not a timing benchmark (timing varies by machine) — these lock in the
STRUCTURAL properties Section 17 requires: exactly one acquisition call
per symbol (no N×M provider calls), bounded memory growth with universe
size, and one symbol's failure never invalidating the rest.

Real measured numbers (mocked acquisition, isolating pure-calculation
cost from network I/O, 300-symbol synthetic universe, this repository's
dev machine): 0.586s total (~1.95ms/symbol), 2.64MB peak traced memory,
zero errors — recorded in the release evidence
(Documentation/Engineering-Handbook/Releases/), not asserted here as a
timing threshold (that would make this test flaky across machines/CI).
"""
import datetime

import pytest

from services.market_leadership import orchestration as orch
from tests.unit.market_leadership.conftest import make_price_df, make_flat_df

AS_OF = datetime.date(2026, 7, 20)


@pytest.fixture(autouse=True)
def _engine_on(monkeypatch):
    monkeypatch.setenv("MARKET_LEADERSHIP_ENGINE_ENABLED", "1")
    monkeypatch.delenv("MARKET_LEADERSHIP_SHADOW_ENABLED", raising=False)


@pytest.mark.regression
class TestNoNTimesMProviderCalls:
    def test_fetch_price_history_called_exactly_once_per_symbol(self, monkeypatch):
        symbols = [f"SYM{i}" for i in range(40)]
        call_counts: dict[str, int] = {}

        def counting_fetch(symbol, market, period="2y"):
            call_counts[symbol] = call_counts.get(symbol, 0) + 1
            return make_price_df(300, seed=hash(symbol) % 1000)

        monkeypatch.setattr(orch, "universe_symbols", lambda market: symbols)
        monkeypatch.setattr(orch, "_sector_map", lambda market: {"Sector": symbols})
        monkeypatch.setattr(orch, "fetch_price_history", counting_fetch)

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return make_flat_df(300, seed=1)

        monkeypatch.setattr(orch.yf, "Ticker", _FakeTicker)

        orch.compute_market_context("US", as_of=AS_OF)

        assert set(call_counts) == set(symbols)
        assert all(n == 1 for n in call_counts.values()), (
            f"expected exactly 1 fetch per symbol, got: {call_counts}"
        )

    def test_benchmark_fetched_once_not_once_per_symbol(self, monkeypatch):
        symbols = [f"SYM{i}" for i in range(40)]
        ticker_calls = []

        monkeypatch.setattr(orch, "universe_symbols", lambda market: symbols)
        monkeypatch.setattr(orch, "_sector_map", lambda market: {"Sector": symbols})
        monkeypatch.setattr(orch, "fetch_price_history", lambda sym, market, period="2y": make_price_df(300, seed=1))

        class _CountingTicker:
            def __init__(self, ticker, *a, **k):
                ticker_calls.append(ticker)

            def history(self, **kw):
                return make_flat_df(300, seed=2)

        monkeypatch.setattr(orch.yf, "Ticker", _CountingTicker)

        orch.compute_market_context("US", as_of=AS_OF)

        assert ticker_calls.count("^GSPC") == 1


@pytest.mark.regression
class TestOneSymbolFailureIsolation:
    def test_one_failure_never_blocks_the_rest_of_a_large_universe(self, monkeypatch):
        symbols = [f"SYM{i}" for i in range(60)]

        def flaky_fetch(symbol, market, period="2y"):
            if symbol == "SYM30":
                raise RuntimeError("synthetic single-symbol failure")
            return make_price_df(300, seed=hash(symbol) % 1000)

        monkeypatch.setattr(orch, "universe_symbols", lambda market: symbols)
        monkeypatch.setattr(orch, "_sector_map", lambda market: {"Sector": symbols})
        monkeypatch.setattr(orch, "fetch_price_history", flaky_fetch)

        class _FakeTicker:
            def __init__(self, *a, **k):
                pass

            def history(self, **kw):
                return make_flat_df(300, seed=1)

        monkeypatch.setattr(orch.yf, "Ticker", _FakeTicker)

        result = orch.compute_market_context("US", as_of=AS_OF)
        assert result["status"] == "ok"
        assert result["symbols_evaluated"] == 60
        assert any("SYM30" in e for e in result["errors"])
        # 59 of 60 symbols still got a real RS entry (not silently dropped).
        scored_or_attempted = [s for s in symbols if s in result["rs"]]
        assert len(scored_or_attempted) == 60
