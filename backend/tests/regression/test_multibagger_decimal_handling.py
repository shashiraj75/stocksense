"""
Product Integrity Workstream #002-series — Multibagger Decimal/float
correction and cross-market response-safety standardization.

Root cause this addresses: stock_fundamentals_cache columns are declared
Postgres NUMERIC, which psycopg returns as decimal.Decimal — but
multibagger_scorecard.py's checklist arithmetic (`roe_avg * 0.8`,
`roe_avg * 0.6`) assumed plain Python floats. Decimal * float raises
TypeError, which the router caught and (before this fix) surfaced as a raw
exception string directly in the API response.

These tests are fully deterministic — no DB, no network, no external
providers. They exercise compute_scorecard()/annotate_and_rank() directly
with Decimal-typed inputs (the exact shape a real Postgres row produces),
and the GET /api/multibagger/screen router with mocked cache/scorecard
calls to verify response-shape safety.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.multibagger_scorecard import compute_scorecard, annotate_and_rank


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — production-like rows with Decimal values, one per market
# ─────────────────────────────────────────────────────────────────────────────

def _decimal_stock_in() -> dict:
    """Same values as tests/conftest.py's multibagger_stock_in fixture, but
    typed as decimal.Decimal — the actual shape a Postgres NUMERIC column
    round-trip produces, which the float-only fixture never exercised."""
    return {
        "symbol": "TEST",
        "roe_pct": Decimal("22.0"),
        "roe_5y_pct": Decimal("20.0"),
        "roce_pct": Decimal("18.0"),
        "sales_growth_3y_pct": Decimal("15.0"),
        "sales_growth_5y_pct": Decimal("13.0"),
        "profit_growth_3y_pct": Decimal("16.0"),
        "profit_growth_5y_pct": Decimal("14.0"),
        "debt_to_equity_pct": Decimal("30.0"),
        "interest_coverage_ratio": Decimal("8.0"),
        "operating_cf_latest_cr": Decimal("150.0"),
        "pe_ratio": Decimal("28.0"),
        "ev_ebitda": Decimal("15.0"),
        "promoter_pledge_pct": Decimal("0.0"),
    }


def _decimal_stock_us() -> dict:
    """US-shaped row: no sales_growth_5y_pct/profit_growth_5y_pct (always
    None for US per multibagger_scorecard.py's own comment), roe_5y_pct
    holds the 4Y average instead."""
    return {
        "symbol": "TEST",
        "roe_pct": Decimal("22.0"),
        "roe_5y_pct": Decimal("20.0"),  # 4Y avg for US
        "roce_pct": Decimal("18.0"),
        "sales_growth_3y_pct": Decimal("15.0"),
        "sales_growth_5y_pct": None,
        "profit_growth_3y_pct": Decimal("16.0"),
        "profit_growth_5y_pct": None,
        "debt_to_equity_pct": Decimal("30.0"),
        "interest_coverage_ratio": Decimal("8.0"),
        "operating_cf_latest_cr": Decimal("150.0"),
        "pe_ratio": Decimal("28.0"),
        "ev_ebitda": Decimal("15.0"),
        "promoter_pledge_pct": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1-2. compute_scorecard() accepts Decimal; the TypeError no longer occurs
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_scorecard_accepts_decimal_in_market():
    """Must not raise TypeError when roe_avg (a Decimal) participates in the
    roe_avg * 0.8 multiplication — this is the exact line that crashed."""
    result = compute_scorecard(_decimal_stock_in(), market="IN")
    assert result["verdict"] == "elite_strong_buy"
    assert result["red_flags"] == []


def test_compute_scorecard_accepts_decimal_us_market():
    """Same check for US, whose roe_5y_pct (4Y avg) hits the identical
    Decimal * float multiplication — confirmed structurally identical to IN
    in the #002-series forensic audit, not a US-specific code path."""
    result = compute_scorecard(_decimal_stock_us(), market="US")
    assert result["verdict"] in ("elite_strong_buy", "strong_buy", "watchlist")
    assert result["max_score"] == 10  # 10 base checks, no IN-only checks for US


def test_compute_scorecard_red_flag_path_accepts_decimal():
    """The second Decimal * float site (roe < roe_avg * 0.6, in the
    red-flag pass) must also not raise."""
    stock = _decimal_stock_in()
    stock["roe_pct"] = Decimal("10.0")  # well below roe_avg * 0.6 = 12.0
    result = compute_scorecard(stock, market="IN")
    assert any("ROE well below" in f for f in result["red_flags"])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Each Multibagger screen can process Decimal-backed rows
# ─────────────────────────────────────────────────────────────────────────────

def test_annotate_and_rank_processes_decimal_rows_in():
    """annotate_and_rank() is the exact function the router calls per
    screen — must not raise across a list of Decimal-typed rows."""
    rows = [_decimal_stock_in(), _decimal_stock_in()]
    rows[1]["symbol"] = "TEST2"
    result = annotate_and_rank(rows, market="IN")
    assert len(result) == 2
    assert all("scorecard" in r for r in result)


def test_annotate_and_rank_processes_decimal_rows_us():
    rows = [_decimal_stock_us(), _decimal_stock_us()]
    rows[1]["symbol"] = "TEST2"
    result = annotate_and_rank(rows, market="US")
    assert len(result) == 2
    assert all("scorecard" in r for r in result)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Missing values remain missing — never coerced to zero
# ─────────────────────────────────────────────────────────────────────────────

def test_none_values_remain_none_not_coerced_to_zero():
    stock = _decimal_stock_in()
    stock["roe_5y_pct"] = None
    result = compute_scorecard(stock, market="IN")
    # roe_avg is None: the "not visibly declining" clause short-circuits to
    # True (per `roe_avg is None or roe >= roe_avg * 0.8`) — this is
    # existing, unchanged behavior; the assertion here is that None does NOT
    # get coerced into Decimal("0")/0.0, which would make `roe >= 0 * 0.8`
    # trivially true for a different, wrong reason.
    roe_check = next(c for c in result["checks"] if c["label"].startswith("ROE > 18%"))
    assert roe_check["passed"] is True

    # A genuinely missing required field must not silently pass as if it
    # were zero — ROCE has no None-safe fallback clause, so None must fail.
    stock2 = _decimal_stock_in()
    stock2["roce_pct"] = None
    result2 = compute_scorecard(stock2, market="IN")
    roce_check = next(c for c in result2["checks"] if c["label"] == "ROCE > 15%")
    assert roce_check["passed"] is False


def test_num_helper_preserves_none():
    from services.multibagger_scorecard import _num
    assert _num(None) is None
    assert _num(Decimal("12.5")) == 12.5
    assert isinstance(_num(Decimal("12.5")), float)
    assert _num(7.0) == 7.0
    assert _num(0) == 0  # int zero preserved, not conflated with None


# ─────────────────────────────────────────────────────────────────────────────
# 5. Thresholds are unchanged
# ─────────────────────────────────────────────────────────────────────────────

def test_thresholds_unchanged_golden_snapshot_still_matches(multibagger_stock_in):
    """Re-runs the pre-existing golden fixture (plain floats) through the
    now-modified compute_scorecard() to confirm the _num() boundary is a
    pure type-safety pass-through — behavior for float inputs is byte-for-
    byte unchanged from before this fix."""
    result = compute_scorecard(multibagger_stock_in, market="IN")
    assert result["verdict"] == "elite_strong_buy"
    assert result["max_score"] == 12
    assert result["score"] == 12
    assert result["red_flags"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 6-7. Router response safety — no raw exception text; zero-result vs failure
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def test_screen_endpoint_success_returns_status_ok(client):
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.query_screen", return_value=[_decimal_stock_in()]), \
         patch("services.fundamentals_cache.last_refreshed", return_value="2026-07-01T10:30:00+00:00"):
        resp = client.get("/api/multibagger/screen", params={"screen": "quality_compounder", "market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["count"] == 1
    assert "error" not in body or body.get("error") is None


def test_screen_endpoint_genuine_zero_result_is_status_ok(client):
    """A successfully-evaluated empty screen (no exception, no rows) must be
    distinguishable from a computation failure — both have count == 0, but
    only the failure path should carry status == 'unavailable'."""
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.query_screen", return_value=[]), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None):
        resp = client.get("/api/multibagger/screen", params={"screen": "quality_compounder", "market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["count"] == 0
    assert body["results"] == []


def test_screen_endpoint_calculation_failure_does_not_expose_raw_exception(client):
    """Simulates the exact real-world failure (Decimal * float TypeError)
    by making query_screen return a row whose type would break naive
    arithmetic, patched via a raising annotate_and_rank to also cover any
    other future internal failure, not just this one bug."""
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.query_screen", return_value=[{"symbol": "X"}]), \
         patch(
             "services.multibagger_scorecard.annotate_and_rank",
             side_effect=TypeError("unsupported operand type(s) for *: 'decimal.Decimal' and 'float'"),
         ):
        resp = client.get("/api/multibagger/screen", params={"screen": "quality_compounder", "market": "US"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["count"] == 0
    assert body["results"] == []
    # The raw Python/library exception text must never reach the client.
    assert "Decimal" not in str(body)
    assert "decimal.Decimal" not in str(body)
    assert "unsupported operand" not in str(body)
    assert body["error"] == "Screen data is temporarily unavailable."


def test_screen_endpoint_failure_is_logged_server_side(client, caplog):
    import logging
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.query_screen", return_value=[{"symbol": "X"}]), \
         patch(
             "services.multibagger_scorecard.annotate_and_rank",
             side_effect=TypeError("unsupported operand type(s) for *: 'decimal.Decimal' and 'float'"),
         ), \
         caplog.at_level(logging.ERROR, logger="api.routers.multibagger"):
        client.get("/api/multibagger/screen", params={"screen": "quality_compounder", "market": "US"})

    assert any("unsupported operand" in r.message or "decimal.Decimal" in r.getMessage()
               or "screen computation failed" in r.message for r in caplog.records), (
        "Technical failure detail must still be captured server-side (logs), "
        "even though it's withheld from the API response."
    )
