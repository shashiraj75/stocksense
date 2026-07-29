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


@pytest.mark.timeout(30)
class TestZeroBarResultNeverFabricatesAnalytics:
    """Stage J1B-Fail-Closed-Hardening, Stage 4/10 -- CALCULATION_
    UNAVAILABLE must prevent the actual MFE/MAE/touch calculator from
    ever running, not merely be a field computed and then ignored. A
    zero-bar provider response still produces a real, persisted,
    LIMITED_EVIDENCE report (an honest manifest-only outcome), but every
    analytic field must be null -- never zero, never fabricated."""

    def test_zero_bars_produces_limited_report_with_all_null_analytics(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_none)

        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "LIMITED_EVIDENCE"

        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        section = structured_report["price_path"]
        assert section["price_path_status"] == "SOURCE_UNAVAILABLE"
        for field in ("mfe_abs", "mfe_pct", "mae_signed_abs", "mae_signed_pct",
                      "mae_magnitude_abs", "mae_magnitude_pct", "target_touch", "stop_touch", "touch_order"):
            assert section[field] is None, f"{field} must be null, never a fabricated zero/value"
        assert structured_report["price_path"]["evidence_quality_decision"]["calculation_status"] == "CALCULATION_UNAVAILABLE"

        # The zero-bar bundle is still an honest, immutable evidence
        # record (never fabricated bars) -- Stage 3's own explicit
        # persistence-permitted decision for SOURCE_UNAVAILABLE.
        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestTrueEvidenceReplayWithoutAnyReport:
    """Stage J1B/Stage 8 — the load-bearing proof that PERSISTED
    EVIDENCE (not an already-complete REPORT) can be replayed to
    CONSTRUCT a missing report. TestCompatibleReplaySurvivesProviderOutage
    below proves report replay (an immutable report already exists);
    this test proves the materially different evidence-replay path: a
    compatible evidence bundle exists, NO price-path report exists yet,
    and the live /generate endpoint must still construct one without
    ever touching the provider."""

    def test_generate_constructs_report_from_persisted_evidence_with_zero_provider_calls(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_generation, price_path_store

        # Price-path disabled during /sell -- the trade closes under
        # Sprint 2 only, exactly the "historical trade predating the
        # price-path evidence layer" shape Stage J exists to handle.
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()

        # Build one real evidence bundle via the actual acquisition
        # function and persist it directly -- simulating evidence that
        # was captured by a prior attempt, independent of any report
        # ever being constructed from it. Bars must be dated within the
        # REAL entry/exit window (module-level _fake_bars is hardcoded
        # to 2026-06-02/03 and would be filtered out entirely by
        # build_price_path_evidence's own entry_date/exit_date bounds
        # check, silently downgrading the bundle to SOURCE_UNAVAILABLE
        # -- caught by real PG CI when the calculation_status assertion
        # below was added).
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": exit_date, "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        persisted_evidence, created = price_path_store.persist_evidence(pg_conn, bundle)
        assert created is True

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1
        report_count_before = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count_before == 0

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        def _raises(*a, **k):
            raise AssertionError("provider function must not be called on a genuine evidence-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        evidence_count_after = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count_after == 1  # no second evidence row created

        report_row = pg_conn.execute(
            "SELECT id, status, structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert report_row is not None
        report_id, report_status, structured_report = report_row
        assert structured_report["price_path"]["price_path_evidence_id"] == persisted_evidence.id

        # Stage J1B-Fail-Closed-Hardening — acquisition provenance must
        # truthfully record that NO provider call occurred for this
        # report, not merely that the endpoint happened to succeed.
        acquisition = structured_report["price_path"]["acquisition_decision"]
        assert acquisition["acquisition_status"] == "COMPATIBLE_REPLAY"
        assert acquisition["provider_call_expected"] is False
        assert acquisition["reused_evidence_id"] == persisted_evidence.id
        assert structured_report["price_path"]["evidence_quality_decision"]["calculation_status"] == "CALCULATION_ELIGIBLE"

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == report_status  # Stage J8 -- report/outbox consistency

        # Repeated generate is idempotent -- no duplicate report/supersession.
        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"
        report_count_final = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count_final == 1


@pytest.mark.timeout(30)
class TestCompatibleReplaySurvivesProviderOutage:
    def test_second_generate_succeeds_even_when_every_provider_function_raises(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        """Stage J5 — a provider outage must never prevent a compatible
        (already-settled) outcome from being returned. A report is
        settled while the provider is still faked (either during
        _open_and_close's own /sell call, per Stage H2A, or by this
        first explicit /generate call — both are a legitimate settled
        outcome); every provider function is then monkeypatched to
        raise, and a second /generate call must still succeed by never
        touching the provider at all."""
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        first = _generate(client, unique_user_id, trade_id)
        assert first.status_code == 200
        assert first.json()["price_path_generation_status"] in (
            "PRICE_PATH_GENERATED", "PRICE_PATH_ALREADY_COMPLETE",
        )

        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise AssertionError("provider function must not be called on a compatible-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"


@pytest.mark.timeout(30)
class TestTransientProviderFailureMarksRetryable:
    def test_provider_exception_marks_outbox_failed_retryable_not_terminal(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        # _open_and_close's own /sell call ALSO synchronously attempts
        # price-path enhancement (Stage H2A) with whatever provider is
        # active at that moment. With the file's autouse fake still
        # active, that call would succeed and settle a COMPLETE report
        # during setup -- this explicit /generate call would then hit
        # the ALREADY_COMPLETE short-circuit and never touch the
        # (raising) provider at all, never exercising the failure this
        # test targets. Disabled only for setup, re-enabled with the
        # raising provider immediately before the explicit call, same
        # pattern as TestTrueConcurrentGenerate in
        # test_price_path_endpoint_lifecycle.py.
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise price_path_acquisition.PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "synthetic outage")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)

        pre_existing = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert pre_existing == 0, "setup itself must not have already generated the price-path report"

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "PROVIDER_FETCH_FAILED"

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0  # no partial/fabricated evidence persisted


@pytest.mark.timeout(30)
class TestMalformedProviderResponseUsesPolicyDrivenOutcome:
    """Stage J1B-Assurance-Closure, Stage 5D -- PROVIDER_UNEXPECTED_
    COLUMN_SHAPE (a malformed/unexpected provider response shape) must
    remain distinguishable from a plain transient PROVIDER_FETCH_FAILED
    outage, and the outcome must come from get_provider_failure_policy,
    not a separate hard-coded action."""

    def test_malformed_response_marks_source_invalid_and_no_evidence_persisted(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise price_path_acquisition.PriceProviderAcquisitionError(
                "PROVIDER_UNEXPECTED_COLUMN_SHAPE", "synthetic malformed response"
            )

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)

        pre_existing = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert pre_existing == 0

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        # Policy-driven: currently retryable (not immediately terminal) --
        # the outbox's own attempt-limit still bounds eventual settlement.
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "PROVIDER_UNEXPECTED_COLUMN_SHAPE"  # distinguishable from PROVIDER_FETCH_FAILED

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 0  # never persisted for a malformed response

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0


@pytest.mark.timeout(30)
class TestUnsupportedCompletenessFailsClosedThroughRealReplay:
    """Stage J1B-Real-PG-Assurance, Stage 5B -- INVALID_SOURCE_DATA has
    no live producer in build_price_path_evidence (verified by direct
    code inspection: no code path in price_path_acquisition.py ever
    assigns STATUS_INVALID_SOURCE_DATA), so the only genuine service-
    boundary way to exercise UNSUPPORTED_EVIDENCE_COMPLETENESS's fail-
    closed behavior is a real persisted evidence row whose own
    data_completeness has been corrupted (simulating legacy or
    externally-damaged data) -- corrupted the same delete+reinsert way
    the entry-snapshot PRESENT_INVALID test does, since this table is
    also immutable at the database level."""

    def test_corrupted_persisted_completeness_produces_no_report(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from psycopg.types.json import Jsonb
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_store

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": exit_date, "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        price_path_store.persist_evidence(pg_conn, bundle)

        # Corrupt the persisted row's data_completeness -- DELETE +
        # re-INSERT with every other column unchanged, since UPDATE is
        # rejected by the table's own immutability trigger.
        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["data_completeness"] = "SOME_LEGACY_VALUE_NO_LONGER_RECOGNIZED"
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        # This table's JSONB columns include both object-valued
        # (source_manifest) and array-valued (limitations, bars) ones --
        # psycopg deserializes a JSONB array to a plain Python list, not
        # a dict, so checking only `isinstance(..., dict)` missed `bars`/
        # `limitations` and left them unadapted (caught by real PG CI on
        # both PG15 and PG17).
        values = tuple(
            Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns
        )
        pg_conn.execute("DELETE FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_price_path_evidence ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0  # never COMPLETE, never a fabricated LIMITED_EVIDENCE report either

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "UNSUPPORTED_EVIDENCE_COMPLETENESS"

        # Still exactly one evidence row -- the corrupted row itself,
        # never repaired, never duplicated.
        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1


@pytest.mark.timeout(30)
class TestFreshAcquisitionProvenanceThroughRealEndpoint:
    """Stage J1B-Real-PG-Assurance, Stage 5A."""

    def test_fresh_acquisition_records_full_provenance(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        pre_existing = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert pre_existing == 0

        from services.postmortem import price_path_acquisition
        calls = {"n": 0}

        def _counted(*a, **k):
            calls["n"] += 1
            return _fake_bars(*a, **k)

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _counted)

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"
        assert calls["n"] == 1

        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        acquisition = structured_report["price_path"]["acquisition_decision"]
        assert acquisition["acquisition_status"] == "ACQUISITION_REQUIRED"
        assert acquisition["provider_call_expected"] is True
        assert acquisition["compatible_evidence_found"] is False
        assert acquisition["reused_evidence_id"] is None

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1
        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 1
        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        # Stage J1B-Final-Reconciliation, Stage 6 -- the exact expected
        # value, not a loose either-or: _open_and_close's manual /buy has
        # no entry snapshot, so the real Sprint 2 prior report is
        # deterministically LIMITED_EVIDENCE, and compute_report_
        # completeness_ceiling's minimum-of-all-ceilings composition means
        # the price-path report (and its outbox settlement) must be too.
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestReportReplaySeparationThroughRealEndpoint:
    """Stage J1B-Real-PG-Assurance, Stage 5I -- an EXISTING compatible
    report must short-circuit before any outbox claim, provider call, or
    evidence lookup -- distinct from evidence replay (Stage 5G/
    TestTrueEvidenceReplayWithoutAnyReport), which still does construct
    a report from persisted evidence."""

    def test_existing_report_never_fabricates_new_provenance(self, client, pg_conn, unique_user_id, monkeypatch):
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        first = _generate(client, unique_user_id, trade_id)
        assert first.status_code == 200

        report_count_before = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        outbox_attempt_before = pg_conn.execute(
            "SELECT attempt_count FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        structured_report_before = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]

        # Stage 6 -- explicitly patch every provider function to fail the
        # test if invoked, proving report replay never even reaches the
        # evidence-lookup layer (distinct from evidence replay, which
        # DOES look up persisted evidence but still never calls the
        # provider).
        from services.postmortem import price_path_acquisition

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on a report-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"

        report_count_after = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        outbox_attempt_after = pg_conn.execute(
            "SELECT attempt_count FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        structured_report_after = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]

        assert report_count_after == report_count_before  # no new report row
        assert outbox_attempt_after == outbox_attempt_before  # no new claim attempt at all
        assert structured_report_after == structured_report_before  # content byte-for-byte unchanged
        # No new acquisition/quality decision block was fabricated for
        # this second call -- the persisted block is exactly the one the
        # FIRST (fresh-acquisition) call wrote.
        assert structured_report_after["price_path"]["acquisition_decision"]["acquisition_status"] == "ACQUISITION_REQUIRED"


@pytest.mark.timeout(30)
class TestReplayImmutabilityByteForByte:
    """Stage J1B-Real-PG-Assurance, Stage 5G -- the persisted evidence
    row used by a genuine evidence-replay generation must remain
    byte-for-byte (field-for-field) identical afterward, not merely
    "still present.\""""

    def test_evidence_row_unchanged_after_replay_generates_a_report(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_store

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": exit_date, "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        price_path_store.persist_evidence(pg_conn, bundle)

        row_before = pg_conn.execute(
            "SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on a genuine evidence-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        row_after = pg_conn.execute(
            "SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert row_before == row_after  # field-for-field identical, not merely "still present"

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1  # no second evidence row


@pytest.mark.timeout(30)
class TestTradeAndSnapshotImmutabilityAcrossPaths:
    """Stage J1B-Real-PG-Assurance, Stage 5H -- for the fresh-acquisition
    and source-unavailable paths (evidence-replay immutability is
    covered separately by TestHistoricalGenerationNeverMutatesSourceRows
    and the byte-for-byte test above)."""

    def test_source_unavailable_path_does_not_modify_trade_or_snapshots(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_none)

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
        assert resp.json()["status"] == "LIMITED_EVIDENCE"

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


@pytest.mark.timeout(30)
class TestContradictoryReplayStateFailsClosedThroughRealEndpoint:
    """Stage J1B-Final-Reconciliation, Stage 3 -- real-PostgreSQL, real
    HTTP-endpoint proof of the explicit fail-closed branch that replaced
    the old `assert evidence is not None`. Test-only forced contradiction
    injection via monkeypatch (never altering production logic to make
    this naturally reachable): classify_replay_or_acquisition is patched
    to claim COMPATIBLE_REPLAY while no compatible evidence row actually
    exists in the database."""

    def test_forced_contradiction_settles_retryable_with_zero_provider_calls(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem import price_path_acquisition, price_path_evidence_decision

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        evidence_count_before = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count_before == 0  # no compatible evidence genuinely exists

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on the integrity-violation path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        fake_acquisition = price_path_evidence_decision.HistoricalEvidenceAcquisitionDecision(
            acquisition_status=price_path_evidence_decision.COMPATIBLE_REPLAY,
            provider_call_expected=False, compatible_evidence_found=True, evidence_id=999999,
        )
        monkeypatch.setattr(
            price_path_evidence_decision, "classify_replay_or_acquisition", lambda **kw: fake_acquisition
        )

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        evidence_count_after = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count_after == 0

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "INTERNAL_INTEGRITY_VIOLATION"


@pytest.mark.timeout(30)
class TestSourceManifestPersistsAllFinalFields:
    """Stage J-F7, item 1 -- the Stage J-F3 manifest fields (symbol
    normalization version, provider-request-start, provider-exclusive-
    end, end-widening reason, boundary-policy version, source-manifest
    schema version, requested trading-weekday count, manifest integrity
    hash) round-trip through real PostgreSQL JSONB storage, not just an
    in-memory dataclass."""

    def test_manifest_fields_survive_real_persistence(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()

        from services.market_hours import ET

        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars_in_window)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        manifest = pg_conn.execute(
            "SELECT source_manifest FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]

        for key in (
            "source_manifest_schema_version", "trade_symbol", "provider_symbol",
            "symbol_normalization_version", "market", "provider_request_start",
            "provider_exclusive_request_end", "end_widening_reason", "boundary_policy_version",
            "requested_trading_weekday_count", "manifest_integrity_hash",
        ):
            assert key in manifest, f"{key} missing from persisted source_manifest"
        assert manifest["provider_exclusive_request_end"] == (exit_date + dt.timedelta(days=1)).isoformat()
        assert manifest["provider_request_start"] == entry_date.isoformat()
        assert manifest["market"] == "US"


@pytest.mark.timeout(30)
class TestCompatibleReplayPreservesManifestUnchanged:
    """Stage J-F7, item 2 -- a compatible-replay report references the
    SAME evidence row, whose manifest (including manifest_integrity_hash)
    is never re-derived or mutated on replay."""

    def test_replay_reuses_identical_persisted_manifest(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()

        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [
                {"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        from services.postmortem import price_path_acquisition, price_path_store, price_path_evidence_decision

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars_in_window)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        evidence, _ = price_path_store.persist_evidence(pg_conn, bundle)
        manifest_before = evidence.source_manifest

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on a compatible-replay path")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        manifest_after, evidence_count = pg_conn.execute(
            "SELECT source_manifest, "
            "(SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s) "
            "FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id, trade_id),
        ).fetchone()
        assert evidence_count == 1  # no second evidence row was ever acquired
        assert manifest_after == manifest_before
        assert manifest_after["manifest_integrity_hash"] == manifest_before["manifest_integrity_hash"]


@pytest.mark.timeout(30)
class TestSameDayTradePersistsNullExcursionAndLimitedEvidence:
    """Stage J-F5/J-F7 item 4 -- a same-day (entry_date == exit_date)
    trade has zero interior bars by construction (price_path_calculator's
    boundary-contamination policy), so MFE/MAE must persist as real NULLs
    in PostgreSQL, never a zero or a guessed value, and the report status
    must be LIMITED_EVIDENCE."""

    def test_same_day_trade_persists_null_excursion(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        # _open_and_close's buy+sell both execute "now" in the same test,
        # so entry_date == exit_date already, with no artificial clock
        # manipulation needed.
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()

        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()
        exit_date = exit_ts.astimezone(ET).date()
        assert entry_date == exit_date, "test precondition: this must be a same-day trade"

        def _same_day_bar(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 108.0, "low": 92.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _same_day_bar)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        structured_report, status = pg_conn.execute(
            "SELECT structured_report, status FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        section = structured_report["price_path"]
        assert section["mfe_abs"] is None
        assert section["mae_signed_abs"] is None
        assert section["mae_magnitude_abs"] is None
        assert section["captured_mfe_pct"] is None
        assert section["giveback_abs"] is None
        assert status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestLegacyInvalidSourceDataRowFallsThroughFailClosed:
    """Stage J-F2/J-F7 item 8 -- a persisted evidence row carrying the
    EXACT legacy value 'INVALID_SOURCE_DATA' (not an arbitrary garbage
    string -- see TestUnsupportedCompletenessFailsClosedThroughRealReplay
    for the general unrecognized-value case, not duplicated here) falls
    through classify_acquired_evidence to UNSUPPORTED_EVIDENCE_
    COMPLETENESS on replay, produces no report, and settles the outbox
    FAILED_RETRYABLE under that status as its own sanitized error code."""

    def test_legacy_invalid_source_data_value_fails_closed_through_real_replay(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from psycopg.types.json import Jsonb
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_store

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        price_path_store.persist_evidence(pg_conn, bundle)

        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["data_completeness"] = "INVALID_SOURCE_DATA"  # the exact legacy value, per J-F2
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        values = tuple(Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns)
        pg_conn.execute("DELETE FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_price_path_evidence ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "UNSUPPORTED_EVIDENCE_COMPLETENESS"


@pytest.mark.timeout(30)
class TestResetScopedToSingleTrade:
    """Stage J-F7 item 9 -- reset removes Stage J evidence/report/outbox
    rows for the resetting user only; a second user's price-path rows
    (evidence, report, outbox) are never touched. TestResetRemovesPrice
    PathRowsWithoutOrphans (test_price_path_endpoint_lifecycle.py) already
    proves the resetting user's own rows reach zero; this test adds the
    cross-user non-interference proof that was missing."""

    def test_reset_does_not_touch_another_users_price_path_rows(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        import uuid

        from tests.postgres_integration.conftest import SYNTHETIC_USER_PREFIX

        unique_user_id_2 = f"{SYNTHETIC_USER_PREFIX}{uuid.uuid4().hex[:16]}"

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        trade_a = _open_and_close(client, pg_conn, unique_user_id)
        _generate(client, unique_user_id, trade_a)
        trade_b = _open_and_close(client, pg_conn, unique_user_id_2)
        _generate(client, unique_user_id_2, trade_b)

        for table in ("paper_trade_price_path_evidence", "paper_trade_postmortem_outbox", "paper_trade_postmortem_report"):
            count = pg_conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id_2,)).fetchone()[0]
            assert count > 0, f"test precondition failed: {table} has no rows for user B"

        resp = client.post("/api/paper-trading/reset?market=ALL", headers=make_auth_header(unique_user_id))
        assert resp.status_code == 200

        for table in ("paper_trade_price_path_evidence", "paper_trade_postmortem_outbox", "paper_trade_postmortem_report"):
            count_a = pg_conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id,)).fetchone()[0]
            assert count_a == 0, f"{table} left an orphan row for the resetting user"
            count_b = pg_conn.execute(f"SELECT count(*) FROM {table} WHERE user_id = %s", (unique_user_id_2,)).fetchone()[0]
            assert count_b > 0, f"{table} was incorrectly wiped for a DIFFERENT user's reset"


def _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts):
    """Directly widens a real trade's opened_at/closed_at to a controlled
    multi-day window -- paper_trades carries no immutability trigger
    (unlike the snapshot/evidence/report tables), so a plain UPDATE is
    sufficient here, distinct from the DELETE+reinsert pattern those
    other tables require."""
    pg_conn.execute(
        "UPDATE paper_trades SET opened_at = %s, closed_at = %s WHERE id = %s",
        (entry_ts, exit_ts, trade_id),
    )


@pytest.mark.timeout(30)
class TestPersistedContaminationExclusion:
    """Stage J Final-Closure-Correction, Stage 6 item 1 -- a provider
    fixture returning a pre-entry bar, valid in-window bars, a post-exit
    bar, AND a current-date bar outside the trade window must persist
    ONLY the in-window bars."""

    def test_only_in_window_bars_are_persisted(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        from services.market_hours import ET

        entry_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 3, 16, 0, tzinfo=ET)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        import datetime as real_dt

        today_outside_window = real_dt.date.today() + real_dt.timedelta(days=365)

        def _contaminated_bars(*a, **k):
            return [
                {"date": dt.date(2026, 5, 29), "open": 90, "high": 92, "low": 89, "close": 91,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # pre-entry
                {"date": dt.date(2026, 6, 1), "open": 100, "high": 102, "low": 99, "close": 101,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # valid
                {"date": dt.date(2026, 6, 2), "open": 101, "high": 103, "low": 100, "close": 102,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # valid
                {"date": dt.date(2026, 6, 3), "open": 102, "high": 104, "low": 101, "close": 103,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # valid
                {"date": dt.date(2026, 6, 4), "open": 103, "high": 105, "low": 102, "close": 104,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # post-exit
                {"date": today_outside_window, "open": 200, "high": 210, "low": 190, "close": 205,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # current-date, outside window
            ]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _contaminated_bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        bars = pg_conn.execute(
            "SELECT bars FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        session_dates = sorted(b["session_date"] for b in bars)
        assert session_dates == ["2026-06-01", "2026-06-02", "2026-06-03"]


@pytest.mark.timeout(30)
class TestBoundaryTouchPersistedAmbiguous:
    """Stage J Final-Closure-Correction, Stage 6 item 2 -- target touched
    only on the entry boundary bar, stop touched only on a later interior
    bar (a DIFFERENT session) -- classify_touch_order's own rule 6
    (boundary touch in a different bar from the other touch) makes this
    genuinely BOUNDARY_BAR_AMBIGUOUS, distinct from a single-sided
    boundary touch (which stays a definitive TARGET_ONLY/STOP_ONLY, per
    the existing unit tests -- not duplicated here).

    NOTE on level_history_complete: classify_touch_order's rule 1 is
    "if not level_history_complete: return LEVEL_HISTORY_INCOMPLETE" --
    the MOST restrictive rule, checked first. paper_trading.py's live
    /generate call site hardcodes level_history_complete=False for
    every real trade today (this codebase's own honest, documented
    finding: it cannot yet prove full stop/target edit history for any
    real trade) -- so LEVEL_HISTORY_INCOMPLETE always wins over
    BOUNDARY_BAR_AMBIGUOUS through the live endpoint's CURRENT call
    site, regardless of the actual touch pattern. That is real,
    verified production behavior, not a test bug -- confirmed by CI
    failure when this test first asserted BOUNDARY_BAR_AMBIGUOUS
    through /generate directly. Proving the ambiguity classifier itself
    against genuine PostgreSQL-persisted evidence (acquired and stored
    through the real endpoint) is therefore done by loading that same
    persisted evidence back via price_path_store and calling
    build_price_path_report_payload directly with
    level_history_complete=True -- still fully grounded in real PG
    data, just not routed through the live endpoint's own current,
    deliberately conservative level-history stance."""

    def test_boundary_and_interior_touch_on_different_bars_is_ambiguous(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id, exit_price=101.0)

        from services.market_hours import ET

        entry_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 4, 16, 0, tzinfo=ET)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)

        pg_conn.execute("UPDATE paper_trades SET stop_loss = %s, target_price = %s WHERE id = %s", (90.0, 120.0, trade_id))

        def _boundary_and_interior_bars(*a, **k):
            return [
                {"date": dt.date(2026, 6, 1), "open": 100, "high": 122, "low": 99, "close": 101,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # entry boundary bar -- target(120) touched
                {"date": dt.date(2026, 6, 2), "open": 101, "high": 103, "low": 100, "close": 102,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # interior, no touch
                {"date": dt.date(2026, 6, 3), "open": 102, "high": 104, "low": 85, "close": 100,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # interior -- stop(90) touched
                {"date": dt.date(2026, 6, 4), "open": 100, "high": 101, "low": 99, "close": 100.5,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # exit boundary bar
            ]

        from services.postmortem import price_path_acquisition, price_path_generation, price_path_store
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _boundary_and_interior_bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        # Confirm the live endpoint's own honest, current stance first.
        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert structured_report["price_path"]["touch_order"] == "LEVEL_HISTORY_INCOMPLETE"

        evidence = price_path_store.get_current_evidence(
            pg_conn, paper_trade_id=trade_id, user_id=unique_user_id,
            evidence_bundle_version="1.0.0", source_id="yfinance_daily", source_version="1.1.0",
        )
        assert evidence is not None
        payload = price_path_generation.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=101.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=trade_id,
        )
        assert payload.price_path_section["touch_order"] == "BOUNDARY_BAR_AMBIGUOUS"
        assert payload.status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestBothSameBarPersistedAmbiguous:
    """Stage J Final-Closure-Correction, Stage 6 item 3 -- stop and target
    both touched within one INTERIOR daily bar -- BOTH_SAME_BAR_AMBIGUOUS,
    against genuine PostgreSQL-persisted evidence, with independently
    valid MFE/MAE still populated (excursion is computed independently
    of touch order).

    Same level_history_complete note as TestBoundaryTouchPersistedAmbiguous
    above -- the live endpoint's own current, honest, hardcoded
    level_history_complete=False means LEVEL_HISTORY_INCOMPLETE always
    wins through /generate directly (confirmed by real CI failure when
    this test first asserted BOTH_SAME_BAR_AMBIGUOUS through /generate);
    the ambiguity classifier is proven directly against the real
    persisted evidence instead."""

    def test_both_same_interior_bar_is_ambiguous_with_excursion_populated(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id, exit_price=100.0)

        from services.market_hours import ET

        entry_ts = dt.datetime(2026, 6, 1, 9, 30, tzinfo=ET)
        exit_ts = dt.datetime(2026, 6, 3, 16, 0, tzinfo=ET)
        _set_trade_window(pg_conn, trade_id, entry_ts, exit_ts)
        pg_conn.execute("UPDATE paper_trades SET stop_loss = %s, target_price = %s WHERE id = %s", (90.0, 120.0, trade_id))

        def _both_same_bar(*a, **k):
            return [
                {"date": dt.date(2026, 6, 1), "open": 100, "high": 101, "low": 99, "close": 100.5,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": dt.date(2026, 6, 2), "open": 100, "high": 125, "low": 85, "close": 100,
                 "volume": 500, "adj_close": None, "dividend": 0.0},  # interior -- BOTH touched here
                {"date": dt.date(2026, 6, 3), "open": 100, "high": 101, "low": 99, "close": 100,
                 "volume": 500, "adj_close": None, "dividend": 0.0},
            ]

        from services.postmortem import price_path_acquisition, price_path_generation, price_path_store
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _both_same_bar)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert structured_report["price_path"]["touch_order"] == "LEVEL_HISTORY_INCOMPLETE"

        evidence = price_path_store.get_current_evidence(
            pg_conn, paper_trade_id=trade_id, user_id=unique_user_id,
            evidence_bundle_version="1.0.0", source_id="yfinance_daily", source_version="1.1.0",
        )
        assert evidence is not None
        payload = price_path_generation.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=100.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=trade_id,
        )
        section = payload.price_path_section
        assert section["touch_order"] == "BOTH_SAME_BAR_AMBIGUOUS"
        assert payload.status == "LIMITED_EVIDENCE"
        assert section["mfe_abs"] == pytest.approx(25.0)  # interior high 125 - entry 100
        assert section["mae_signed_abs"] == pytest.approx(-15.0)  # interior low 85 - entry 100


@pytest.mark.timeout(30)
class TestInvalidExitSnapshotThroughRealEndpoint:
    """Stage J Final Semantic Reconciliation, Stage 7 -- a PRESENT_
    INVALID exit snapshot (market-mismatched -- corrupted via the same
    immutable DELETE+reinsert method the entry-snapshot equivalent test
    in this file already uses) caps the report at LIMITED_EVIDENCE AND
    now persists an explicit, deterministic structured reason
    (trade_context_decision.reason_codes contains EXIT_CONTEXT_INVALID,
    folded into evidence_gaps too) -- closing the gap the prior pass
    found and honestly worked around: eligibility.limitations was
    computed but never actually reached evidence_gaps or the structured
    report before this pass wired persist_price_path_report's new
    trade_context_decision parameter."""

    def test_invalid_exit_snapshot_persists_explicit_structured_reason(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from psycopg.types.json import Jsonb

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        # Market mismatch (not a trade_id substitution -- a trade_id
        # substitution makes _fetch_exit_snapshot(trade_id) return None,
        # which is MISSING, not PRESENT_INVALID; mirrors the working
        # entry-snapshot pattern in TestMissingSnapshotCapsReportAt
        # LimitedEvidence above) -- the row is still found BY trade_id,
        # but fails market-equality validation, producing a genuine
        # PRESENT_INVALID (EXIT_CONTEXT_INVALID) classification.
        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["market"] = "IN" if row["market"] == "US" else "US"
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        values = tuple(Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns)
        pg_conn.execute("DELETE FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_exit_snapshot ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        status, structured_report, evidence_gaps = pg_conn.execute(
            "SELECT status, structured_report, evidence_gaps FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert status != "COMPLETE"
        assert status == "LIMITED_EVIDENCE"

        trade_context_decision = structured_report["price_path"]["trade_context_decision"]
        assert "EXIT_CONTEXT_INVALID" in trade_context_decision["reason_codes"]
        assert any("EXIT_CONTEXT_INVALID" == g or "exit snapshot row exists" in g for g in evidence_gaps)

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestFinalManifestIntegrityThroughRealPostgres:
    """Stage J Final-Closure-Correction, Stage 6 item 5 -- persists
    evidence, recomputes and verifies manifest_integrity_hash from the
    actual PostgreSQL JSONB value (not an in-memory dict), then proves a
    tampered COPY of that same manifest fails verification."""

    def test_manifest_hash_verifies_from_real_jsonb_and_tamper_fails(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem.price_path_acquisition import verify_source_manifest_integrity

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 108.0, "low": 92.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars_in_window)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        manifest_from_db = pg_conn.execute(
            "SELECT source_manifest FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]

        assert verify_source_manifest_integrity(manifest_from_db) is True

        tampered = dict(manifest_from_db)
        tampered["unresolved_basis_limitations"] = tampered.get("unresolved_basis_limitations", []) + ["injected"]
        assert verify_source_manifest_integrity(tampered) is False


@pytest.mark.timeout(30)
class TestNoBarsManifestConsistencyThroughRealPostgres:
    """Stage J Final-Closure-Correction, Stage 6 item 6 -- for a
    no-valid-bars acquisition, the persisted bundle's own `limitations`
    column and its `source_manifest.unresolved_basis_limitations` must
    match exactly, through real PostgreSQL storage (not just in-memory,
    which TestBundleAndManifestLimitationsIdentical::test_no_bars already
    proves at the unit level -- this is the real-JSONB-round-trip
    counterpart)."""

    def test_persisted_limitations_and_manifest_match(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        monkeypatch.setattr(
            __import__("services.postmortem.price_path_acquisition", fromlist=["x"]),
            "fetch_raw_daily_bars", _fake_none,
        )
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        limitations, manifest = pg_conn.execute(
            "SELECT limitations, source_manifest FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s",
            (trade_id,),
        ).fetchone()
        assert list(limitations) == list(manifest["unresolved_basis_limitations"])
        assert "no valid bars were returned by the provider for the requested window" in limitations


@pytest.mark.timeout(30)
class TestZeroBarLifecycleThroughRealEndpoint:
    """Stage J Final Semantic Reconciliation, Stage 1 -- corrects a real
    documentation defect found by direct audit: rows 2/21 of the Stage J
    matrix previously claimed the zero-bar fresh-acquisition path settles
    the outbox FAILED_RETRYABLE with no report -- that was never true.
    FAILED_RETRYABLE-with-no-report is reserved for SOURCE_INVALID/
    UNSUPPORTED_EVIDENCE_COMPLETENESS (which have fresh_persistence_
    permitted=False). SOURCE_UNAVAILABLE (fresh_persistence_permitted=
    True) always persists its honest zero-bar evidence row and produces
    an honest LIMITED_EVIDENCE report via build_unavailable_report_
    payload -- confirmed by the pre-existing regression test
    test_zero_bars_never_invokes_the_calculator, which this real-PG test
    reproduces end-to-end through the actual HTTP endpoint and real
    PostgreSQL storage."""

    def test_zero_bars_persists_unavailable_evidence_and_limited_report(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)

        from services.postmortem import price_path_acquisition, price_path_generation

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _fake_none)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        calls = {"excursion": 0, "touches": 0, "order": 0}

        def _count(name):
            def _inner(*a, **k):
                calls[name] += 1
                raise AssertionError(f"{name} must never be invoked for zero-bar evidence")
            return _inner

        monkeypatch.setattr(price_path_generation, "compute_excursion", _count("excursion"))
        monkeypatch.setattr(price_path_generation, "detect_touches", _count("touches"))
        monkeypatch.setattr(price_path_generation, "classify_touch_order", _count("order"))

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"
        assert calls == {"excursion": 0, "touches": 0, "order": 0}

        evidence_count, data_completeness = pg_conn.execute(
            "SELECT count(*), max(data_completeness) FROM paper_trade_price_path_evidence "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert evidence_count == 1
        assert data_completeness == "UNAVAILABLE"

        report_count, status, structured_report = pg_conn.execute(
            "SELECT count(*) OVER (), status, structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert report_count == 1
        assert status == "LIMITED_EVIDENCE"
        section = structured_report["price_path"]
        for field in (
            "mfe_abs", "mfe_pct", "mae_signed_abs", "mae_signed_pct", "mae_magnitude_abs",
            "mae_magnitude_pct", "target_touch", "stop_touch", "touch_order",
            "giveback_abs", "captured_mfe_pct",
        ):
            assert section[field] is None, f"{field} must be null for zero-bar evidence, not fabricated"

        outbox_status = pg_conn.execute(
            "SELECT status FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestLegacyManifestReplayFailsClosedThroughRealEndpoint:
    """Stage J Final Semantic Reconciliation, Stage 2 -- a manifest hash
    the live replay path never checks is not a production integrity
    control. This proves it IS load-bearing end-to-end: a persisted
    evidence row whose manifest lacks the Stage J-F3 fields (simulating
    a row acquired before manifest-integrity governance existed) is
    rejected on replay -- zero provider calls, zero calculator calls,
    zero new evidence, zero report, the original row left completely
    unchanged, outbox FAILED_RETRYABLE under the sanitized
    MANIFEST_INTEGRITY_VIOLATION code."""

    def test_legacy_manifest_replay_fails_closed(self, client, pg_conn, unique_user_id, monkeypatch):
        from psycopg.types.json import Jsonb
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_store

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_date = entry_ts.astimezone(ET).date()

        def _bars_in_window(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 108.0, "low": 99.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars_in_window, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        price_path_store.persist_evidence(pg_conn, bundle)

        # Corrupt the persisted manifest down to a legacy (pre-Stage-J-F3)
        # shape -- DELETE + re-INSERT, since this table is immutable.
        with pg_conn.cursor() as cur:
            cur.execute("SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["source_manifest"] = {
            "source_id": "yfinance_daily", "source_type": "EXTERNAL_UNOFFICIAL_DAILY",
            "provider": "yfinance", "auto_adjust": False,
        }  # legacy shape -- missing manifest_integrity_hash and every Stage J-F3 field
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        values = tuple(Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns)
        pg_conn.execute("DELETE FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
        pg_conn.execute(
            f"INSERT INTO paper_trade_price_path_evidence ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on a manifest-integrity-failed replay")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        evidence_count, stored_manifest = pg_conn.execute(
            "SELECT count(*) OVER (), max(source_manifest::text) FROM paper_trade_price_path_evidence "
            "WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert evidence_count == 1  # never repaired, never duplicated

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "MANIFEST_INTEGRITY_VIOLATION"


def _open_and_close_market(client, pg_conn, user_id, *, market, symbol, exit_price):
    """Symmetric counterpart to _open_and_close, supporting both IN and
    US -- seeds both cash columns generously so either market's buy
    preflight succeeds regardless of which currency column the backend
    actually debits for that market."""
    from tests.postgres_integration.conftest import ensure_portfolio

    ensure_portfolio(pg_conn, user_id, cash=100_000_000.0, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id, symbol=symbol, market=market, price=100.0).json()["trade_id"]
    resp = _sell(client, user_id, trade_id, price=exit_price)
    assert resp.status_code == 200
    return trade_id


@pytest.mark.timeout(30)
class TestIndiaCompleteAcquisition:
    """Stage J3, Stage 6A -- India (RELIANCE, market=IN) fresh acquisition
    through the real endpoint, real PostgreSQL storage."""

    def test_india_acquisition_persists_correct_manifest_and_identity(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from services.postmortem.price_path_acquisition import verify_source_manifest_integrity

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)
        entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

        from services.market_hours import IST

        entry_date = entry_ts.astimezone(IST).date()
        received_symbols = []

        def _bars(provider_symbol, *a, **k):
            received_symbols.append(provider_symbol)
            return [{"date": entry_date, "open": 2800.0, "high": 2850.0, "low": 2780.0, "close": 2820.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"
        assert received_symbols == ["RELIANCE.NS"]

        evidence_count, market, provider_symbol, source_version, market_timezone, manifest = pg_conn.execute(
            "SELECT count(*) OVER (), market, provider_symbol, source_version, market_timezone, source_manifest "
            "FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert evidence_count == 1
        assert market == "IN"
        assert provider_symbol == "RELIANCE.NS"
        assert source_version == "1.1.0"
        assert market_timezone == "Asia/Kolkata"
        assert manifest["trade_symbol"] == "RELIANCE"
        assert manifest["provider_symbol"] == "RELIANCE.NS"
        assert manifest["market"] == "IN"
        assert manifest["source_version"] == "1.1.0"
        assert verify_source_manifest_integrity(manifest) is True

        status, outbox_status = pg_conn.execute(
            "SELECT r.status, o.status FROM paper_trade_postmortem_report r "
            "JOIN paper_trade_postmortem_outbox o ON o.paper_trade_id = r.paper_trade_id "
            "WHERE r.paper_trade_id = %s AND r.report_schema_version = '1.1.0' "
            "AND o.requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        # Manual /buy has no entry snapshot -- deterministically LIMITED_EVIDENCE,
        # same reasoning as the existing US fresh-acquisition test.
        assert status == "LIMITED_EVIDENCE"
        assert outbox_status == "LIMITED_EVIDENCE"


@pytest.mark.timeout(30)
class TestIndiaEvidenceReplay:
    """Stage J3, Stage 6B -- India evidence replay: zero provider calls,
    evidence reused unchanged, idempotent repeat request."""

    def test_india_replay_zero_provider_calls_and_idempotent(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)
        entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

        from services.market_hours import IST

        entry_date = entry_ts.astimezone(IST).date()

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 2800.0, "high": 2850.0, "low": 2780.0, "close": 2820.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        first = _generate(client, unique_user_id, trade_id)
        assert first.status_code == 200
        assert first.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on replay")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1


@pytest.mark.timeout(30)
class TestUSCompleteAcquisition:
    """Stage J3, Stage 6C -- US (AAPL, market=US) fresh acquisition
    through the real endpoint, real PostgreSQL storage, including one
    DST-date case."""

    @pytest.mark.parametrize("use_dst_date", [False, True])
    def test_us_acquisition_persists_correct_manifest_and_identity(
        self, client, pg_conn, unique_user_id, monkeypatch, use_dst_date,
    ):
        from services.postmortem.price_path_acquisition import verify_source_manifest_integrity

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        from services.market_hours import ET

        if use_dst_date:
            # 2026-07-15 is deep in US daylight-saving time (EDT, UTC-4);
            # forcing the trade window here (instead of relying on
            # whatever "now" happens to be) is what actually makes this
            # a real DST-date case rather than an unused parameter.
            dst_ts = dt.datetime(2026, 7, 15, 12, 0, tzinfo=ET)
            _set_trade_window(pg_conn, trade_id, dst_ts, dst_ts)
            entry_ts = dst_ts
        else:
            entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

        entry_date = entry_ts.astimezone(ET).date()
        received_symbols = []

        def _bars(provider_symbol, *a, **k):
            received_symbols.append(provider_symbol)
            return [{"date": entry_date, "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"
        assert received_symbols == ["AAPL"]  # no .NS suffix for US

        market, provider_symbol, source_version, market_timezone, manifest = pg_conn.execute(
            "SELECT market, provider_symbol, source_version, market_timezone, source_manifest "
            "FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()
        assert market == "US"
        assert provider_symbol == "AAPL"
        assert source_version == "1.1.0"
        assert market_timezone == "America/New_York"
        assert manifest["trade_symbol"] == "AAPL"
        assert manifest["provider_symbol"] == "AAPL"
        assert manifest["market"] == "US"
        assert verify_source_manifest_integrity(manifest) is True


@pytest.mark.timeout(30)
class TestUSEvidenceReplay:
    """Stage J3, Stage 6D -- US evidence replay, same requirements as India."""

    def test_us_replay_zero_provider_calls_and_idempotent(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)
        entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]

        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        first = _generate(client, unique_user_id, trade_id)
        assert first.status_code == 200
        assert first.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on replay")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)

        second = _generate(client, unique_user_id, trade_id)
        assert second.status_code == 200
        assert second.json()["price_path_generation_status"] == "PRICE_PATH_ALREADY_COMPLETE"

        evidence_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,)
        ).fetchone()[0]
        assert evidence_count == 1


@pytest.mark.timeout(30)
class TestMarketIsolation:
    """Stage J3, Stage 6E -- India and US evidence for the SAME user never
    cross-contaminate: symbol suffix, timezone, and market column all
    agree per row, and each row's own compatible-evidence lookup can
    never accidentally match the other market's row."""

    def test_india_and_us_evidence_remain_isolated(self, client, pg_conn, unique_user_id, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        in_trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="IN", symbol="RELIANCE", exit_price=101.0)
        us_trade_id = _open_and_close_market(client, pg_conn, unique_user_id, market="US", symbol="AAPL", exit_price=101.0)

        in_entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (in_trade_id,)).fetchone()[0]
        us_entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (us_trade_id,)).fetchone()[0]

        from services.market_hours import ET, IST

        in_entry_date = in_entry_ts.astimezone(IST).date()
        us_entry_date = us_entry_ts.astimezone(ET).date()

        def _bars_for(provider_symbol, *a, **k):
            if provider_symbol.endswith(".NS"):
                return [{"date": in_entry_date, "open": 2800.0, "high": 2850.0, "low": 2780.0, "close": 2820.0,
                          "volume": 500, "adj_close": None, "dividend": 0.0}]
            return [{"date": us_entry_date, "open": 190.0, "high": 195.0, "low": 188.0, "close": 192.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars_for)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        assert _generate(client, unique_user_id, in_trade_id).status_code == 200
        assert _generate(client, unique_user_id, us_trade_id).status_code == 200

        rows = pg_conn.execute(
            "SELECT paper_trade_id, market, provider_symbol, market_timezone FROM paper_trade_price_path_evidence "
            "WHERE paper_trade_id IN (%s, %s) ORDER BY paper_trade_id", (in_trade_id, us_trade_id),
        ).fetchall()
        by_trade = {r[0]: r for r in rows}

        in_row = by_trade[in_trade_id]
        assert in_row[1] == "IN"
        assert in_row[2].endswith(".NS")
        assert in_row[3] == "Asia/Kolkata"

        us_row = by_trade[us_trade_id]
        assert us_row[1] == "US"
        assert not us_row[2].endswith(".NS")
        assert us_row[3] == "America/New_York"

        # Cross-market lookup never matches -- India's evidence is never
        # returned as compatible evidence for the US trade or vice versa.
        from services.postmortem import price_path_store
        cross_lookup = price_path_store.get_current_evidence(
            pg_conn, paper_trade_id=us_trade_id, user_id=unique_user_id,
            evidence_bundle_version="1.0.0", source_id="yfinance_daily", source_version="1.1.0",
        )
        assert cross_lookup is not None
        assert cross_lookup.market == "US"
        assert not cross_lookup.provider_symbol.endswith(".NS")


def _insert_legacy_1_0_0_evidence_row(pg_conn, trade_id, user_id, entry_date, exit_date):
    """Directly inserts a pre-Stage-J3 evidence row under source_version
    1.0.0 with a Stage-J-F3-complete manifest EXCEPT still declaring
    source_version="1.0.0" in both the row and its manifest -- simulates
    a row genuinely acquired before this pass's version bump, not merely
    a corrupted current-version row (that scenario is already covered by
    TestLegacyManifestReplayFailsClosedThroughRealEndpoint)."""
    from services.market_hours import ET
    from services.postmortem import price_path_acquisition, price_path_store

    def _bars(*a, **k):
        return [{"date": entry_date, "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0,
                  "volume": 500, "adj_close": None, "dividend": 0.0}]

    bundle = price_path_acquisition.acquire_price_path_evidence(
        paper_trade_id=trade_id, user_id=user_id, symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=dt.datetime(entry_date.year, entry_date.month, entry_date.day, 12, tzinfo=ET),
        exit_timestamp=dt.datetime(exit_date.year, exit_date.month, exit_date.day, 12, tzinfo=ET),
        fetch_bars_fn=_bars, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
    )
    evidence, _created = price_path_store.persist_evidence(pg_conn, bundle)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
        columns = [d.name for d in cur.description]
        row = dict(zip(columns, cur.fetchone()))
    row["source_version"] = "1.0.0"
    manifest = dict(row["source_manifest"])
    manifest["source_version"] = "1.0.0"
    from services.postmortem.price_path_acquisition import finalize_source_manifest
    row["source_manifest"] = finalize_source_manifest(manifest, manifest["unresolved_basis_limitations"])
    insert_columns = [c for c in columns if c not in ("id", "created_at")]
    from psycopg.types.json import Jsonb
    values = tuple(Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns)
    pg_conn.execute("DELETE FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s", (trade_id,))
    pg_conn.execute(
        f"INSERT INTO paper_trade_price_path_evidence ({', '.join(insert_columns)}) "
        f"VALUES ({', '.join('%s' for _ in insert_columns)})",
        values,
    )


@pytest.mark.timeout(30)
class TestLegacyAndCurrentEvidenceCoexistence:
    """Stage J3, Stage 7 items 1 and 6 -- an existing immutable 1.0.0
    evidence row never blocks a fresh 1.1.0 acquisition; both rows
    coexist, the old one byte-for-byte unchanged, and the current report
    is built from the 1.1.0 row."""

    def test_legacy_row_does_not_block_fresh_current_acquisition(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        from services.market_hours import ET

        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        _insert_legacy_1_0_0_evidence_row(pg_conn, trade_id, unique_user_id, entry_date, exit_date)
        legacy_row_before = pg_conn.execute(
            "SELECT id, source_manifest::text FROM paper_trade_price_path_evidence "
            "WHERE paper_trade_id = %s AND source_version = '1.0.0'", (trade_id,)
        ).fetchone()
        assert legacy_row_before is not None

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_GENERATED"

        rows = pg_conn.execute(
            "SELECT source_version FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s ORDER BY source_version",
            (trade_id,),
        ).fetchall()
        assert [r[0] for r in rows] == ["1.0.0", "1.1.0"]

        legacy_row_after = pg_conn.execute(
            "SELECT id, source_manifest::text FROM paper_trade_price_path_evidence "
            "WHERE paper_trade_id = %s AND source_version = '1.0.0'", (trade_id,)
        ).fetchone()
        assert legacy_row_after == legacy_row_before  # byte-for-byte unchanged

        structured_report = pg_conn.execute(
            "SELECT structured_report FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert structured_report["price_path"]["acquisition_decision"]["acquisition_status"] == "ACQUISITION_REQUIRED"

    def test_legacy_report_and_current_report_coexist(self, client, pg_conn, unique_user_id, monkeypatch):
        """Stage 7 item 2 -- simulates an existing 1.0.0-identity report
        row (Sprint 2's own seed, which already uses a distinct schema/
        calc identity from the 1.1.0 price-path report) plus a new 1.1.0
        report; both remain, distinct calculation identities, no
        supersession corruption of the Sprint 2 row."""
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        sprint2_report_id = pg_conn.execute(
            "SELECT id FROM paper_trade_postmortem_report WHERE paper_trade_id = %s AND report_schema_version = '1.0.0'",
            (trade_id,),
        ).fetchone()[0]

        entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]
        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        reports = pg_conn.execute(
            "SELECT id, report_schema_version, calculation_version FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s ORDER BY id", (trade_id,),
        ).fetchall()
        schema_versions = {r[1] for r in reports}
        assert schema_versions == {"1.0.0", "1.1.0"}
        calc_versions = {r[2] for r in reports}
        assert len(calc_versions) == 2  # distinct calculation identities

        sprint2_still_present = any(r[0] == sprint2_report_id for r in reports)
        assert sprint2_still_present

    def test_legacy_and_current_outbox_rows_coexist(self, client, pg_conn, unique_user_id, monkeypatch):
        """Stage 7 item 3 -- the price-path outbox row's requested
        identity contains src:1.1.0; any pre-existing outbox row (e.g.
        Sprint 2's own, or a hypothetical prior price-path attempt under
        an older identity) is never claimed by the current request."""
        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts = pg_conn.execute("SELECT opened_at FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()[0]
        from services.market_hours import ET

        entry_date = entry_ts.astimezone(ET).date()

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        from services.postmortem import price_path_acquisition
        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _bars)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200

        outbox_rows = pg_conn.execute(
            "SELECT requested_report_schema_version, requested_calculation_version FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s", (trade_id,),
        ).fetchall()
        price_path_rows = [r for r in outbox_rows if r[0] == "1.1.0"]
        assert len(price_path_rows) == 1
        assert "src:1.1.0" in price_path_rows[0][1]


@pytest.mark.timeout(30)
class TestCurrentVersionCorruptManifestFailsClosedAlongsideLegacyRow:
    """Stage J3, Stage 7 item 5 -- combines a legacy 1.0.0 row (present,
    correctly ignored) with a CORRUPTED current-version 1.1.0 row: no
    provider call, no calculator, no report, sanitized integrity
    failure, both rows left completely unchanged."""

    def test_corrupt_current_manifest_fails_closed_with_legacy_row_present(
        self, client, pg_conn, unique_user_id, monkeypatch,
    ):
        from psycopg.types.json import Jsonb
        from services.market_hours import ET
        from services.postmortem import price_path_acquisition, price_path_store

        monkeypatch.delenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", raising=False)
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        entry_ts, exit_ts = pg_conn.execute(
            "SELECT opened_at, closed_at FROM paper_trades WHERE id = %s", (trade_id,)
        ).fetchone()
        entry_date, exit_date = entry_ts.astimezone(ET).date(), exit_ts.astimezone(ET).date()

        _insert_legacy_1_0_0_evidence_row(pg_conn, trade_id, unique_user_id, entry_date, exit_date)

        def _bars(*a, **k):
            return [{"date": entry_date, "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0,
                      "volume": 500, "adj_close": None, "dividend": 0.0}]

        bundle = price_path_acquisition.acquire_price_path_evidence(
            paper_trade_id=trade_id, user_id=unique_user_id, symbol="AAPL", market="US",
            market_timezone_name="America/New_York", market_tzinfo=ET,
            entry_timestamp=entry_ts, exit_timestamp=exit_ts,
            fetch_bars_fn=_bars, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none,
        )
        price_path_store.persist_evidence(pg_conn, bundle)

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s AND source_version = '1.1.0'",
                (trade_id,),
            )
            columns = [d.name for d in cur.description]
            row = dict(zip(columns, cur.fetchone()))
        row["source_manifest"] = {"source_id": "yfinance_daily"}  # corrupted -- missing everything else
        insert_columns = [c for c in columns if c not in ("id", "created_at")]
        values = tuple(Jsonb(row[c]) if isinstance(row[c], (dict, list)) else row[c] for c in insert_columns)
        pg_conn.execute(
            "DELETE FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s AND source_version = '1.1.0'",
            (trade_id,),
        )
        pg_conn.execute(
            f"INSERT INTO paper_trade_price_path_evidence ({', '.join(insert_columns)}) "
            f"VALUES ({', '.join('%s' for _ in insert_columns)})",
            values,
        )

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on a corrupted current-version replay")

        monkeypatch.setattr(price_path_acquisition, "fetch_raw_daily_bars", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_split_events", _raises)
        monkeypatch.setattr(price_path_acquisition, "fetch_dividend_events", _raises)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        resp = _generate(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["price_path_generation_status"] == "PRICE_PATH_FAILED_RETRYABLE"

        evidence_rows = pg_conn.execute(
            "SELECT source_version FROM paper_trade_price_path_evidence WHERE paper_trade_id = %s ORDER BY source_version",
            (trade_id,),
        ).fetchall()
        assert [r[0] for r in evidence_rows] == ["1.0.0", "1.1.0"]  # both unchanged, no repair, no duplication

        report_count = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_report "
            "WHERE paper_trade_id = %s AND report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()[0]
        assert report_count == 0

        outbox_status, error_code = pg_conn.execute(
            "SELECT status, last_error_code FROM paper_trade_postmortem_outbox "
            "WHERE paper_trade_id = %s AND requested_report_schema_version = '1.1.0'", (trade_id,)
        ).fetchone()
        assert outbox_status == "FAILED_RETRYABLE"
        assert error_code == "MANIFEST_INTEGRITY_VIOLATION"
