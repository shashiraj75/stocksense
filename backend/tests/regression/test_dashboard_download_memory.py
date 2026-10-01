"""Exercise real yfinance dispatch without network: refreshes must not retain threads."""
import threading

import multitasking
import pandas as pd
import pytest
import yfinance.multi

from services import heatmap_service, screener_service


@pytest.mark.parametrize("consumer", ["movers", "heatmap"])
def test_repeated_dashboard_downloads_do_not_spawn_retained_tasks(monkeypatch, consumer):
    caller = threading.get_ident()
    executing_threads = []

    def download_one(ctx, ticker, *args, **kwargs):
        executing_threads.append(threading.get_ident())
        closes = {"AAPL": [100.0, 110.0], "MSFT": [100.0, 90.0],
                  "MISSING": [float("nan"), float("nan")]}
        frame = pd.DataFrame({"Close": closes[ticker]},
                             index=pd.date_range("2026-09-28", periods=2))
        with ctx.lock:
            ctx.dfs[ticker] = frame
        return frame

    monkeypatch.setattr(yfinance.multi, "_download_one", download_one)
    retained_before = len(multitasking.config["TASKS"])
    for _ in range(5):
        symbols = ["AAPL", "MSFT", "MISSING"]
        if consumer == "movers":
            result = screener_service._bulk_quotes(symbols)
            assert result["AAPL"]["change_pct"] == 10.0
            assert result["MSFT"]["change_pct"] == -10.0
            assert "MISSING" not in result
        else:
            result = heatmap_service._bulk_changes(symbols, "")
            assert result["AAPL"] == {"change_pct": 10.0, "change": 10.0}
            assert result["MSFT"] == {"change_pct": -10.0, "change": -10.0}
            assert result["MISSING"] is None

    assert len(executing_threads) == 15
    assert set(executing_threads) == {caller}
    assert len(multitasking.config["TASKS"]) == retained_before
