"""
Trade Postmortem Sprint 3A, Stage J10 — dedicated real-PostgreSQL
historical-compatibility tests.

These specifically exercise the Stage J2/J3 snapshot-validity and
completeness-ceiling governance against REAL entry/exit snapshot rows
(deleted after a real buy/sell to simulate a historical trade that
predates Sprint 2's durability layer, or mutated to simulate a
present-but-invalid row) — not the fake-conn simulations in
tests/regression/test_paper_trading_price_path_lease_lifecycle.py,
and not merely re-asserting what the Stage A endpoint suite in
test_price_path_endpoint_lifecycle.py already covers (cross-user
isolation, idempotent replay, reset cleanup are already proven there
and are not duplicated here).
"""
import datetime as dt

import pytest

from tests.postgres_integration.conftest import make_auth_header

pytestmark = pytest.mark.postgres_integration


def _fake_bars(*a, **k):
    return [
        {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        {"date": dt.date(2026, 6, 3), "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
    ]


def _fake_none(*a, **k):
    return []


@pytest.fixture(autouse=True)
def _patch_price_path_provider(monkeypatch):
    from services.postmortem import price_path_acquisition
    monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_bars)
    monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _fake_none)
    monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _fake_none)


@pytest.fixture(autouse=True)
def _enable_price_path_flag(monkeypatch):
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 110.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _generate(client, user_id, trade_id):
    return client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=make_auth_header(user_id))


def _open_and_close(client, pg_conn, user_id, exit_price=110.0):
    from tests.postgres_integration.conftest import ensure_portfolio
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


@pytest.mark.timeout(30)
class TestMissingSnapshotCapsReportAtLimitedEvidence:
    def test_missing_entry_snapshot_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "LIMITED_EVIDENCE"

    def test_missing_exit_snapshot_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("DELETE FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"

    def test_invalid_entry_snapshot_market_mismatch_produces_limited_evidence(self, client, pg_conn, unique_user_id):
        """A present-but-invalid row (wrong market — simulating a
        corrupted or cross-context row) must never be used, and must
        still cap the ceiling exactly like a missing row.

        paper_trade_entry_snapshot rows are immutable at the database
        level (a genuine Sprint 2 hardening trigger rejects UPDATE) --
        the only way to substitute an invalid row is DELETE + re-INSERT
        with the same column values except market, using the real row's
        own data so every NOT NULL column stays populated."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        from psycopg.types.json import Jsonb

        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["market"] = "IN" if row["market"] == "US" else "US"
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        # verification_levels/recommendation_reasoning are JSONB and come
        # back from psycopg as plain dicts — re-inserting a bare dict
        # against a %s placeholder has no adapter; Jsonb() wraps it.
        values = tuple(
            Jsonb(row[c]) if isinstance(row[c], dict) else row[c] for c in insert_columns
        )
        pg_conn.execute("DELETE FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_entry_snapshot ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestMissingExitPriceCapsCompleteness:
    def test_missing_exit_price_caps_report_at_limited_evidence(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        pg_conn.execute("UPDATE paper_trades SET exit_price = NULL WHERE id = %s", (trade_id,))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestHistoricalGenerationNeverMutatesSourceRows:
    def test_generation_does_not_modify_trade_entry_or_exit_snapshot_rows(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        trade_before = pg_conn.execute(
            "SELECT symbol, market, entry_price, exit_price, status, opened_at, closed_at "
            "FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_before = pg_conn.execute(
            "SELECT simulated_execution_price, captured_at FROM paper_trade_entry_snapshot "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        exit_before = pg_conn.execute(
            "SELECT * FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        trade_after = pg_conn.execute(
            "SELECT symbol, market, entry_price, exit_price, status, opened_at, closed_at "
            "FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_after = pg_conn.execute(
            "SELECT simulated_execution_price, captured_at FROM paper_trade_entry_snapshot "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        exit_after = pg_conn.execute(
            "SELECT * FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()

        assert trade_before == trade_after
        assert entry_before == entry_after
        assert exit_before == exit_after
