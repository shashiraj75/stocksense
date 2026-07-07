"""
Paper Trading closed-history payload efficiency — regression tests for
GET /api/paper-trading/portfolio's include_full_closed_trades parameter.
Confirms: (1) omitting the parameter preserves exact legacy behavior
(closed_trades present), (2) include_full_closed_trades=false omits the
full closed_trades array entirely and returns the compact
closed_trade_overview_by_market instead, (3) the modern path never issues
a full "SELECT ... paper_trades WHERE user_id = %s ORDER BY opened_at DESC"
scan (only a bounded open-trades query plus two aggregate/windowed
queries), so it never materializes every closed trade.
"""
import datetime
import time
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-jwt-secret-at-least-32-bytes-long"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _auth(sub: str = "user-aaa") -> dict:
    token = jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class _RecordingConn:
    """Same helper pattern as the existing paper-trading test suites — pops
    one canned fetchall() result per execute() call, in call order."""
    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _closed_trade_row(id, market="IN", horizon="short", exit_reason="TARGET_HIT"):
    closed = datetime.datetime(2026, 1, id, tzinfo=datetime.timezone.utc)
    return (id, "AAPL", market, 1, 100.0, 110.0, "CLOSED", "BUY", horizon,
            closed, closed, 90.0, 120.0, "manual", exit_reason)


def _open_trade_row(id, market="IN"):
    opened = datetime.datetime(2026, 1, id, tzinfo=datetime.timezone.utc)
    return (id, "AAPL", market, 1, 100.0, None, "OPEN", "BUY", "short",
            opened, None, 90.0, 120.0, "manual", None)


@pytest.mark.regression
class TestLegacyPortfolioDefault:
    def test_omitting_parameter_returns_full_closed_trades(self, client):
        # Legacy single-query path: one _conn() call, one SELECT of every
        # trade (open + closed) for the user.
        conn = _RecordingConn(
            fetchone_results=[(1_000_000.0, 100_000.0, True)],
            fetchall_results=[[_closed_trade_row(1), _open_trade_row(2)]],
        )
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get("/api/paper-trading/portfolio", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert "closed_trades" in body
        assert len(body["closed_trades"]) == 1
        assert body["closed_trades"][0]["id"] == 1
        # Additive field present too, derived from the same in-memory list —
        # not a behavior change for legacy consumers who simply ignore it.
        assert body["closed_trade_overview_by_market"]["IN"]["closed_trade_count"] == 1

    def test_explicit_true_matches_omitted_parameter(self, client):
        conn = _RecordingConn(
            fetchone_results=[(1_000_000.0, 100_000.0, True)],
            fetchall_results=[[_closed_trade_row(1)]],
        )
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/portfolio",
                params={"include_full_closed_trades": "true"},
                headers=_auth(),
            )
        assert resp.status_code == 200
        assert "closed_trades" in resp.json()


@pytest.mark.regression
class TestModernPortfolioPath:
    def test_false_omits_full_closed_trades_array(self, client):
        conn = _RecordingConn(
            fetchone_results=[(1_000_000.0, 100_000.0, True)],
            fetchall_results=[
                [],   # open trades
                [],   # aggregates
                [],   # latest-per-bucket window query
            ],
        )
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/portfolio",
                params={"include_full_closed_trades": "false"},
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "closed_trades" not in body

    def test_modern_response_includes_compact_overview_with_correct_counts(self, client):
        agg_row_short = ("IN", "short", 5, 3, 1, 4, 250.0, 500.0, 8.0)  # market, bucket, count, hit, stop, win, pnl, invested, avg_ret
        conn = _RecordingConn(
            fetchone_results=[(1_000_000.0, 100_000.0, True)],
            fetchall_results=[
                [],                # open trades
                [agg_row_short],   # aggregates
                [_closed_trade_row(1)],  # latest-per-bucket rows
            ],
        )
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/portfolio",
                params={"include_full_closed_trades": "false"},
                headers=_auth(),
            )
        body = resp.json()
        overview = body["closed_trade_overview_by_market"]["IN"]
        assert overview["closed_trade_count"] == 5
        assert overview["win_trades_count"] == 4
        assert overview["win_rate_pct"] == 80.0
        assert overview["total_invested"] == 500.0

        bucket = body["closed_trade_history_by_horizon"]["IN"]["short"]
        assert bucket["summary"]["closed_trade_count"] == 5
        assert bucket["summary"]["target_hit_count"] == 3
        assert bucket["summary"]["stop_loss_count"] == 1
        assert bucket["earlier_trade_count"] == 4  # 5 total - 1 latest row returned
        assert len(bucket["latest_trades"]) == 1

    def test_modern_path_issues_no_full_closed_trade_scan(self, client):
        # Confirms the modern path's SQL never mirrors the legacy
        # unconditional "every trade for this user" query — only a bounded
        # open-trades query plus the two aggregate/windowed queries.
        conn = _RecordingConn(
            fetchone_results=[(1_000_000.0, 100_000.0, True)],
            fetchall_results=[[], [], []],
        )
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            client.get(
                "/api/paper-trading/portfolio",
                params={"include_full_closed_trades": "false"},
                headers=_auth(),
            )
        # calls[0] is _ensure_portfolio's own SELECT (unrelated to closed
        # trades); the modern path's three queries follow it.
        assert len(conn.calls) == 4
        open_sql, _ = conn.calls[1]
        aggregate_sql, _ = conn.calls[2]
        windowed_sql, _ = conn.calls[3]
        assert "status = 'OPEN'" in open_sql
        assert "GROUP BY market, bucket" in aggregate_sql
        assert "status = 'CLOSED'" in aggregate_sql
        assert "ROW_NUMBER()" in windowed_sql
        assert "status = 'CLOSED'" in windowed_sql
        # Unlike the legacy path's single unconditional
        # "WHERE user_id = %s ORDER BY opened_at DESC" scan (no status
        # filter at all, fetching every open AND closed trade), every
        # modern-path query is scoped by an explicit status filter.
        for sql in (open_sql, aggregate_sql, windowed_sql):
            assert "WHERE user_id = %s ORDER BY opened_at DESC" not in sql
