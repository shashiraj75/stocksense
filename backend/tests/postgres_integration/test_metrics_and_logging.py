"""
Real PostgreSQL Verification, CI Execution phase — Part 16.

Verifies the operational instrumentation actually fires during real Buy
flows against a real disposable PostgreSQL instance, and that sensitive
values never appear in captured log output.
"""
import logging

import pytest

from services.postmortem import idempotency_metrics as metrics
from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

pytestmark = pytest.mark.postgres_integration


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset_for_tests()
    yield
    metrics.reset_for_tests()


@pytest.mark.timeout(30)
class TestMetricsFireDuringRealBuyFlows:
    def test_fresh_reservation_and_completion_counters_increment(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": "metrics-fresh-key"},
            headers=make_auth_header(unique_user_id),
        )
        counters = metrics.get_snapshot()["counters"]
        assert counters.get(metrics.COUNTER_FRESH_RESERVATION, 0) >= 1
        assert counters.get(metrics.COUNTER_COMPLETED, 0) >= 1

    def test_replay_and_duplicate_prevented_counters_increment(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": "metrics-replay-key"}
        client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(unique_user_id))
        client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(unique_user_id))
        counters = metrics.get_snapshot()["counters"]
        assert counters.get(metrics.COUNTER_REPLAYED, 0) >= 1
        assert counters.get(metrics.COUNTER_DUPLICATE_TRADE_PREVENTED, 0) >= 1

    def test_fingerprint_conflict_counter_increments(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        key = "metrics-conflict-key"
        client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": key},
            headers=make_auth_header(unique_user_id),
        )
        client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 2, "price": 101.0, "idempotency_key": key},
            headers=make_auth_header(unique_user_id),
        )
        assert metrics.get_snapshot()["counters"].get(metrics.COUNTER_FINGERPRINT_CONFLICT, 0) >= 1

    def test_buy_transaction_duration_recorded(self, client, pg_conn, unique_user_id):
        ensure_portfolio(pg_conn, unique_user_id)
        client.post(
            "/api/paper-trading/buy",
            json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": "metrics-duration-key"},
            headers=make_auth_header(unique_user_id),
        )
        durations = metrics.get_snapshot()["durations"]
        assert durations.get(metrics.DURATION_BUY_TRANSACTION, {}).get("count", 0) >= 1


@pytest.mark.timeout(30)
class TestSensitiveDataNeverLogged:
    def test_real_buy_flow_logs_never_contain_the_raw_idempotency_key(self, client, pg_conn, unique_user_id, caplog):
        raw_key = "super-secret-raw-key-should-never-appear-in-logs"
        ensure_portfolio(pg_conn, unique_user_id)
        with caplog.at_level(logging.DEBUG):
            client.post(
                "/api/paper-trading/buy",
                json={"symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0, "idempotency_key": raw_key},
                headers=make_auth_header(unique_user_id),
            )
            # Fingerprint-mismatch path logs a warning with the key_ref hash — confirm the raw key still never appears.
            client.post(
                "/api/paper-trading/buy",
                json={"symbol": "AAPL", "market": "US", "quantity": 2, "price": 101.0, "idempotency_key": raw_key},
                headers=make_auth_header(unique_user_id),
            )
        for record in caplog.records:
            assert raw_key not in record.getMessage()

    def test_real_buy_flow_logs_never_contain_response_body_or_reasoning(self, client, pg_conn, unique_user_id, caplog):
        ensure_portfolio(pg_conn, unique_user_id)
        with caplog.at_level(logging.DEBUG):
            client.post(
                "/api/paper-trading/buy",
                json={
                    "symbol": "AAPL", "market": "US", "quantity": 1, "price": 101.0,
                    "idempotency_key": "metrics-reasoning-key",
                    "entry_evidence": {"recommendation_reasoning": [
                        {"indicator": "RSI", "signal": "BUY", "reason": "a distinctive unlikely-to-collide phrase XYZQ123"}
                    ]},
                },
                headers=make_auth_header(unique_user_id),
            )
        for record in caplog.records:
            assert "XYZQ123" not in record.getMessage()
