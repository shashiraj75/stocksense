"""
2026-09 — GET /paper-trading/pnl-by-period, the new Daily/Weekly/Monthly/
Yearly P&L breakdown endpoint for the Paper Trading overview page.

Uses dependency_overrides for auth (simplest override pattern already used
elsewhere in this test suite, e.g. test_safe_error_responses.py) and
patches api.routers.paper_trading._conn with an in-memory fake returning
fixed rows — no real database.
"""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _fake_conn(rows):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.execute.return_value.fetchall.return_value = rows
    return conn


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from services.auth import get_current_user_id

    app.dependency_overrides[get_current_user_id] = lambda: "test-user-id"
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user_id, None)


def test_merges_in_and_us_rows_for_the_same_period(client, monkeypatch):
    import api.routers.paper_trading as pt
    from datetime import datetime, timezone

    period_a = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [
        ("IN", period_a, 3, 1500.0),
        ("US", period_a, 2, -200.0),
    ]
    monkeypatch.setattr(pt, "_conn", lambda: _fake_conn(rows))

    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "month"})
    assert res.status_code == 200
    body = res.json()
    assert body["period"] == "month"
    assert len(body["buckets"]) == 1
    bucket = body["buckets"][0]
    assert bucket["in_realized_pnl"] == 1500.0
    assert bucket["in_trade_count"] == 3
    assert bucket["us_realized_pnl_usd"] == -200.0
    assert bucket["us_trade_count"] == 2


def test_periods_with_only_one_market_do_not_leak_the_other_markets_zero_as_missing(client, monkeypatch):
    import api.routers.paper_trading as pt
    from datetime import datetime, timezone

    period_a = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [("IN", period_a, 1, 100.0)]
    monkeypatch.setattr(pt, "_conn", lambda: _fake_conn(rows))

    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "day"})
    bucket = res.json()["buckets"][0]
    assert bucket["in_realized_pnl"] == 100.0
    assert bucket["in_trade_count"] == 1
    assert bucket["us_realized_pnl_usd"] == 0.0
    assert bucket["us_trade_count"] == 0


def test_buckets_are_ordered_most_recent_first(client, monkeypatch):
    import api.routers.paper_trading as pt
    from datetime import datetime, timezone

    older = datetime(2026, 7, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [("IN", newer, 1, 10.0), ("IN", older, 1, 20.0)]
    monkeypatch.setattr(pt, "_conn", lambda: _fake_conn(rows))

    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "month"})
    buckets = res.json()["buckets"]
    assert buckets[0]["period_start"] > buckets[1]["period_start"]


def test_limit_is_applied_after_merging_across_markets(client, monkeypatch):
    import api.routers.paper_trading as pt
    from datetime import datetime, timezone

    rows = []
    for i in range(5):
        d = datetime(2026, 1 + i, 1, tzinfo=timezone.utc)
        rows.append(("IN", d, 1, 10.0))
        rows.append(("US", d, 1, 5.0))
    monkeypatch.setattr(pt, "_conn", lambda: _fake_conn(rows))

    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "month", "limit": 2})
    buckets = res.json()["buckets"]
    assert len(buckets) == 2


def test_invalid_period_value_is_rejected_with_422(client):
    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "fortnight"})
    assert res.status_code == 422


def test_period_field_only_accepts_the_four_supported_granularities():
    import inspect
    import api.routers.paper_trading as pt
    sig = inspect.signature(pt.get_pnl_by_period)
    period_annotation = str(sig.parameters["period"].annotation)
    for granularity in ("day", "week", "month", "year"):
        assert granularity in period_annotation


def test_zero_closed_trades_returns_an_empty_bucket_list(client, monkeypatch):
    import api.routers.paper_trading as pt
    monkeypatch.setattr(pt, "_conn", lambda: _fake_conn([]))

    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "day"})
    assert res.status_code == 200
    assert res.json()["buckets"] == []


def test_requires_authentication():
    from api.main import app
    client = TestClient(app)  # no dependency override — real auth path
    res = client.get("/api/paper-trading/pnl-by-period", params={"period": "day"})
    assert res.status_code in (401, 403)
