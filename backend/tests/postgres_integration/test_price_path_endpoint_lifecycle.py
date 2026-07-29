"""
Trade Postmortem Sprint 3A, Stage A — real PostgreSQL verification of
the ACTUAL HTTP endpoints (/sell, /postmortem/{trade_id}/generate,
GET /postmortem/{trade_id}) exercising the price-path leased-outbox
lifecycle, including TRUE multi-threaded concurrency (not sequential
simulation) against a real database.

No local PostgreSQL is available in this development environment (no
docker/psql/pg_ctl/initdb, nothing on :5432 — verified explicitly
before writing this file). This suite is only actually executed by the
GitHub Actions PostgreSQL 15/17 CI matrix; its first real execution
against a live server happens once this branch is pushed and
dispatched. Collection was verified locally; execution was not.

Every test here monkeypatches the module-level provider functions in
services.postmortem.price_path_acquisition (fetch_raw_daily_bars/
fetch_split_events/fetch_dividend_events) — the SAME functions
_attempt_price_path_enhancement imports by default when no override is
supplied — so the real HTTP endpoints exercise the real leased
lifecycle end-to-end without ever making a live yfinance call.
"""
import datetime as dt
import threading

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, get_portfolio_cash, make_auth_header

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
    """Applies to every test in this file — the default (no-override)
    provider path the real endpoints use is fully faked, never live."""
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
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


@pytest.mark.timeout(30)
class TestSellEndpointPricePathWiring:
    def test_sell_commits_and_price_path_outbox_visible_before_response_returns(self, client, pg_conn, unique_user_id):
        """Because the price-path flag is enabled and the provider is
        faked (instant), by the time /sell's HTTP response returns, the
        SAME connection this test uses can already observe: the trade
        CLOSED, cash credited, the Sprint 2 outbox settled, and (since
        _attempt_price_path_enhancement runs synchronously post-commit
        in this test's configuration) a price-path outbox row too."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        status = pg_conn.execute("SELECT status FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]
        assert status == "CLOSED"
        assert get_portfolio_cash(pg_conn, unique_user_id, market="US") > 0

        outbox_rows = pg_conn.execute(
            "SELECT requested_report_schema_version, status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s ORDER BY requested_report_schema_version", (trade_id,)
        ).fetchall()
        schema_versions = [r[0] for r in outbox_rows]
        assert "1.0.0" in schema_versions  # Sprint 2
        assert "1.1.0" in schema_versions  # Sprint 3A price-path

    def test_provider_failure_preserves_sell_response_and_marks_outbox_retryable(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.postmortem import price_path_acquisition
        from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError

        def _failing(*a, **k):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _failing)

        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_id = _buy(client, unique_user_id).json()["trade_id"]
        resp = _sell(client, unique_user_id, trade_id, price=120.0)
        assert resp.status_code == 200  # the sell itself is unaffected

        status = pg_conn.execute("SELECT status FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]
        assert status == "CLOSED"

        pp_outbox = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert pp_outbox is not None
        assert pp_outbox[0] == "FAILED_RETRYABLE"

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 0


@pytest.mark.timeout(30)
class TestGenerateEndpointPricePathWiring:
    def test_generate_upgrades_sprint2_report_to_price_path(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["effective_report_version"] == "1.1.0"
        assert body["price_path_generation_status"] in ("PRICE_PATH_GENERATED", "PRICE_PATH_ALREADY_COMPLETE")

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert report_count == 2  # Sprint 2 + price-path

    def test_repeated_generate_makes_no_second_provider_call(self, client, pg_conn, unique_user_id, monkeypatch):
        from services.postmortem import price_path_acquisition
        calls = {"n": 0}

        def _counted(*a, **k):
            calls["n"] += 1
            return _fake_bars()

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _counted)

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        first = _generate(client, unique_user_id, trade_id).json()
        calls_after_first = calls["n"]
        second = _generate(client, unique_user_id, trade_id).json()
        assert second["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"
        assert calls["n"] == calls_after_first  # no new provider call

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert report_count == 2

    def test_cross_user_generate_indistinguishable_from_nonexistent(self, client, pg_conn, unique_user_id):
        victim_id = f"{unique_user_id}-victim"
        trade_id = _open_and_close(client, pg_conn, victim_id)

        not_found = _generate(client, unique_user_id, 999999999)
        cross_user = _generate(client, unique_user_id, trade_id)
        assert cross_user.status_code == 404
        assert cross_user.json() == not_found.json()


@pytest.mark.timeout(30)
class TestTrueConcurrentGenerate:
    def test_two_simultaneous_generate_requests_one_provider_call_one_report(self, client, pg_conn, unique_user_id, monkeypatch):
        """TRUE concurrency: two real threads issue real HTTP requests
        against the real TestClient/database simultaneously (started
        together, no artificial sequencing) — not a sequential
        simulation of a fake row's status. Only the winning claimant
        ever reaches the provider call at all (the loser is rejected at
        the atomic outbox claim itself, before Phase 2) — a small sleep
        inside the provider widens that request's own window without
        requiring both threads to arrive, which a 2-party barrier would
        incorrectly assume."""
        from services.postmortem import price_path_acquisition
        import time as _time

        calls = {"n": 0}
        calls_lock = threading.Lock()

        def _slow_bars(*a, **k):
            with calls_lock:
                calls["n"] += 1
            _time.sleep(0.3)  # widen the race window for the second thread
            return _fake_bars()

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _slow_bars)

        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        results = []
        results_lock = threading.Lock()

        def _attempt():
            resp = _generate(client, unique_user_id, trade_id)
            with results_lock:
                results.append(resp.json())

        threads = [threading.Thread(target=_attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        assert len(results) == 2
        statuses = [r["price_path_generation_status"] for r in results]
        # Both may observe GENERATED (one truly generated; the other's
        # own claim attempt lost the race and, once the winner commits,
        # a subsequent internal re-check would find it ALREADY_COMPLETE
        # — but a request that lost the claim race outright returns
        # GENERATION_IN_PROGRESS/NOT_YET_AVAILABLE, never a duplicate
        # GENERATED). The key invariants are the DURABLE ones below.
        assert statuses.count("PRICE_PATH_GENERATED") <= 1
        assert calls["n"] == 1  # exactly one provider invocation across both requests

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 1  # exactly one price-path report, never two

        outbox_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_count == 1  # exactly one deterministic price-path outbox row

    def test_active_lease_returns_in_progress_not_a_duplicate_claim(self, client, pg_conn, unique_user_id, monkeypatch):
        """A request arriving while another holds the lease must observe
        a stable status, never race a concurrent duplicate generation."""
        from services.postmortem import price_path_acquisition

        release_event = threading.Event()

        def _blocking_bars(*a, **k):
            release_event.wait(timeout=10)
            return _fake_bars()

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _blocking_bars)

        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        first_result = {}

        def _first():
            first_result["resp"] = _generate(client, unique_user_id, trade_id).json()

        t1 = threading.Thread(target=_first)
        t1.start()

        # Give the first request time to claim the lease and block inside
        # the provider call before the second one starts.
        import time as _time
        _time.sleep(1.0)

        second_resp = _generate(client, unique_user_id, trade_id).json()
        release_event.set()
        t1.join(timeout=15)

        assert second_resp["price_path_generation_status"] in (
            "PRICE_PATH_GENERATION_IN_PROGRESS", "PRICE_PATH_ALREADY_COMPLETE",
        )
        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 1


@pytest.mark.timeout(30)
class TestGetRemainsReadOnlyWithPricePathEnabled:
    def test_get_creates_no_price_path_rows_even_with_flag_enabled(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        before_outbox = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        before_evidence = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]

        resp = client.get(f"/api/paper-trading/postmortem/{trade_id}", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        after_outbox = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        after_evidence = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert after_outbox == before_outbox
        assert after_evidence == before_evidence


@pytest.mark.timeout(30)
class TestResetRemovesPricePathRowsWithoutOrphans:
    def test_full_reset_removes_price_path_evidence_and_outbox_and_report(self, client, pg_conn, unique_user_id):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        _generate(client, unique_user_id, trade_id)

        resp = client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        for table in ("paper_trade_price_path_evidence", "paper_trade_postmortem_outbox", "paper_trade_postmortem_report"):
            count = pg_conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id,)).fetchone()[0]
            assert count == 0, f"{table} left an orphan row after reset"
