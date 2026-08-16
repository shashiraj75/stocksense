"""
Trade Postmortem Engine, Phase 1 — proves the P&L-calculation refactor
(services/paper_trade_math.py extraction) left GET /portfolio's
_trade_row_to_dict and POST /sell's response byte-for-byte identical to
their pre-refactor inline formulas.

Old formulas being locked in here, verbatim, as the ground truth:
  _trade_row_to_dict:  round((xp - ep) * qty, 2) if xp else 0.0
  paper_sell pnl:      round((price - ep) * qty, 2)
  paper_sell pnl_pct:  round((price - ep) / ep * 100, 2) if ep and ep > 0 else 0
"""
import datetime as dt
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from api.routers.paper_trading import _trade_row_to_dict, _TRADE_ROW_COLUMNS


def _old_realized_pnl(ep, xp, qty):
    return round((xp - ep) * qty, 2) if xp else 0.0


def _make_row(ep, xp, qty=10, status="CLOSED"):
    # Column order per _TRADE_ROW_COLUMNS:
    # id, symbol, market, quantity, entry_price, exit_price, status, signal,
    # horizon, opened_at, closed_at, stop_loss, target_price,
    # trade_management_mode, exit_reason
    return (
        1, "AAPL", "US", qty, ep, xp, status, "BUY", "medium",
        dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
        90.0, 130.0, "manual", "MANUAL",
    )


@pytest.mark.regression
class TestTradeRowToDictPnlParity:
    @pytest.mark.parametrize("ep,xp,qty", [
        (100.0, 120.0, 10),
        (100.0, 90.0, 10),
        (100.0, 100.0, 10),
        (33.33, 44.44, 7),
        (0.1, 0.2, 3),
    ])
    def test_matches_pre_refactor_formula(self, ep, xp, qty):
        row = _make_row(ep, xp, qty)
        trade = _trade_row_to_dict(row)
        assert trade["realized_pnl"] == _old_realized_pnl(ep, xp, qty)

    def test_null_exit_price_on_a_closed_row_matches_old_fallback(self):
        row = _make_row(100.0, None, 10)
        trade = _trade_row_to_dict(row)
        assert trade["realized_pnl"] == _old_realized_pnl(100.0, None, 10) == 0.0

    def test_open_trade_has_no_realized_pnl_key_unchanged(self):
        row = _make_row(100.0, None, 10, status="OPEN")
        trade = _trade_row_to_dict(row)
        assert "realized_pnl" not in trade


class _RecordingConn:
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


@pytest.mark.regression
class TestPaperSellPnlParity:
    @pytest.mark.parametrize("ep,price,qty", [
        (100.0, 120.0, 10),
        (100.0, 90.0, 10),
        (33.33, 44.44, 7),
    ])
    def test_sell_response_matches_old_formulas(self, ep, price, qty):
        import jwt
        import time
        from fastapi.testclient import TestClient

        secret = "regression-test-jwt-secret-at-least-32-bytes-long"
        supabase_url = "https://test-project.supabase.co"

        with patch.dict("os.environ", {"SUPABASE_JWT_SECRET": secret, "SUPABASE_URL": supabase_url}):
            from api.main import app
            client = TestClient(app)
            token = jwt.encode(
                {"sub": "user-aaa", "aud": "authenticated", "iss": f"{supabase_url}/auth/v1",
                 "exp": time.time() + 3600},
                secret, algorithm="HS256",
            )

            # Trade Postmortem Engine, Sprint 2 — paper_sell now issues the
            # market-open probe first, then delegates to close_service.
            # close_paper_trade for the row-locked ownership/status SELECT,
            # the closing UPDATE (RETURNING id), the exit-snapshot INSERT,
            # and the outbox INSERT — none of the latter three need a
            # fetchone result (INSERT ... RETURNING id is consumed too,
            # but only the outbox one is; the exit-snapshot INSERT has no
            # RETURNING clause and never calls fetchone()).
            recorder = _RecordingConn(fetchone_results=[
                ("US",),
                ("user-aaa", "AAPL", qty, ep, "OPEN", "US", 90.0, 130.0, "manual", None, None, None, None),
                (1,),
                (1,),
            ])

            @contextmanager
            def _fake_conn():
                yield recorder

            with patch.object(
                __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
            ), patch("api.routers.paper_trading._is_market_open", return_value=True):
                resp = client.post(
                    "/api/paper-trading/sell/1",
                    json={"price": price},
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert resp.status_code == 200
        body = resp.json()
        old_pnl = round((price - ep) * qty, 2)
        old_pnl_pct = round((price - ep) / ep * 100, 2) if ep and ep > 0 else 0
        assert body["pnl"] == old_pnl
        assert body["pnl_pct"] == old_pnl_pct
