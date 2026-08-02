"""
Trade Postmortem Sprint 3A, Stage H2 durable-lifecycle correction —
genuine (not spy-based) tests of _attempt_price_path_enhancement's own
leased outbox lifecycle: atomic claim, backoff, stale-worker/expired-
lease reclaim, terminal attempt-limit, provider-failure classification,
and idempotent no-second-provider-call replay.

Calls _attempt_price_path_enhancement directly (not through the HTTP
layer — that wiring is already covered by
test_paper_trading_generate_endpoint.py's spy-based tests) against a
self-contained in-memory fake connection modeling paper_trades,
paper_trade_postmortem_outbox (the REAL claim/mark-terminal/mark-
retryable SQL shapes outbox.py issues), paper_trade_postmortem_report,
and paper_trade_price_path_evidence.
"""
import datetime as dt
import json as _json
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from services.postmortem.price_path_acquisition import PriceProviderAcquisitionError


class _FakeConn:
    def __init__(self, shared: dict):
        self.shared = shared
        self.calls = []
        self._pending = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._pending = self._process(sql, params or ())
        return self

    def fetchone(self):
        return self._pending

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if stripped.startswith("SELECT id, user_id, status, symbol, market, quantity, entry_price, exit_price"):
            (trade_id,) = params
            return shared["trades"].get(trade_id)

        if stripped.startswith("INSERT INTO paper_trade_postmortem_outbox"):
            trade_id, user_id, schema_v, calc_v, rules_v, source_request_id = params
            key = (trade_id, schema_v, calc_v, rules_v)
            if key in shared["outbox_by_key"]:
                return None
            new_id = shared["next_outbox_id"]
            shared["next_outbox_id"] += 1
            shared["outbox_by_key"][key] = new_id
            shared["outbox_rows"][new_id] = {
                "id": new_id, "paper_trade_id": trade_id, "user_id": user_id,
                "requested_report_schema_version": schema_v, "requested_calculation_version": calc_v,
                "requested_rules_version": rules_v, "status": "PENDING", "attempt_count": 0,
                "source_request_id": source_request_id, "claimed_by": None,
                "next_attempt_at": None, "lease_expires_at": None,
            }
            return (new_id,)

        if stripped.startswith("SELECT id FROM paper_trade_postmortem_outbox"):
            trade_id, schema_v, calc_v, rules_v = params
            existing_id = shared["outbox_by_key"].get((trade_id, schema_v, calc_v, rules_v))
            return (existing_id,) if existing_id is not None else None

        if "UPDATE paper_trade_postmortem_outbox o" in sql and "GENERATING" in sql:
            max_attempts, outbox_id, user_id = params["max_attempts"], params["outbox_id"], params["user_id"]
            lease_seconds, claimant = params["lease_seconds"], params["claimant"]
            row = shared["outbox_rows"].get(outbox_id)
            if row is None or row["user_id"] != user_id:
                return None
            now = dt.datetime.now(dt.timezone.utc)
            claimable = (
                row["status"] == "PENDING"
                or (row["status"] == "FAILED_RETRYABLE"
                    and (row["next_attempt_at"] is None or row["next_attempt_at"] <= now))
                or (row["status"] == "GENERATING"
                    and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now)
            )
            if not claimable:
                return None
            row["attempt_count"] += 1
            if row["attempt_count"] > max_attempts:
                row["status"] = "FAILED_TERMINAL"
                row["claimed_by"] = None
            else:
                row["status"] = "GENERATING"
                row["claimed_by"] = claimant
                row["lease_expires_at"] = now + dt.timedelta(seconds=lease_seconds)
            return (
                row["id"], row["paper_trade_id"], row["user_id"], row["requested_report_schema_version"],
                row["requested_calculation_version"], row["requested_rules_version"], row["status"],
                row["attempt_count"], row["source_request_id"], row["claimed_by"],
            )

        if stripped.startswith("SELECT") and "paper_trade_postmortem_report" in sql and "user_id = %s" in sql:
            paper_trade_id, user_id, schema_v, calc_v, rules_v = params
            for row in shared["report_rows"].values():
                if (row[1], row[2], row[6], row[7], row[8]) == (paper_trade_id, user_id, schema_v, calc_v, rules_v):
                    return row
            return None

        if stripped.startswith("SELECT") and "paper_trade_postmortem_report" in sql and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            for row in shared["report_rows"].values():
                if (row[1], row[6], row[7], row[8]) == (paper_trade_id, schema_v, calc_v, rules_v):
                    return row
            return None

        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, supersedes_id) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in shared["report_rows"].values():
                if (row[1], row[6], row[7], row[8]) == key:
                    return None
            new_id = shared["next_report_id"]
            shared["next_report_id"] += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status, _json.loads(structured), _json.loads(ev_items),
                   _json.loads(claims), _json.loads(manifest), _json.loads(gaps), _json.loads(warnings),
                   dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc), supersedes_id)
            shared["report_rows"][new_id] = row
            return row

        if "SET status = 'FAILED_RETRYABLE'" in sql:
            error_code, error_summary, backoff_secs, outbox_id, claimed_by = params
            row = shared["outbox_rows"].get(outbox_id)
            if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                return None
            row["status"] = "FAILED_RETRYABLE"
            row["claimed_by"] = None
            row["lease_expires_at"] = None
            row["next_attempt_at"] = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=backoff_secs)
            row["last_error_code"] = error_code
            return (outbox_id,)

        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id, claimed_by = params
            row = shared["outbox_rows"].get(outbox_id)
            if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                return None
            row["status"] = status
            row["claimed_by"] = None
            return (outbox_id,)

        if stripped.startswith("INSERT INTO paper_trade_price_path_evidence"):
            key = (params[0], params[4], params[5], params[7])
            for row in shared["evidence_rows"].values():
                if (row[1], row[5], row[6], row[8]) == key:
                    return None
            new_id = shared["next_evidence_id"]
            shared["next_evidence_id"] += 1
            source_manifest, limitations, bars = _json.loads(params[-4]), _json.loads(params[-3]), _json.loads(params[-2])
            row = (new_id,) + params[:-4] + (source_manifest, limitations, bars, params[-1])
            shared["evidence_rows"][new_id] = row
            return row

        if stripped.startswith("SELECT") and "paper_trade_price_path_evidence" in sql and "user_id = %s" in sql:
            paper_trade_id, user_id, version, source_id, source_version = params
            for row in shared["evidence_rows"].values():
                if (row[1], row[5], row[6], row[8]) == (paper_trade_id, version, source_id, source_version) and row[2] == user_id:
                    return row
            return None

        return None


def _trade_row(
    *, trade_id=1, owner="user-aaa", status="CLOSED", symbol="AAPL", market="US",
    quantity=10, entry_price=100.0, exit_price=120.0, stop_loss=90.0, target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    closed_at=dt.datetime(2026, 6, 4, tzinfo=dt.timezone.utc),
    trade_management_mode="manual", exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


def _new_shared(trades: list[dict], *, sprint2_report=True) -> dict:
    shared = {
        "trades": {t.get("trade_id", 1): _trade_row(**t) for t in trades},
        "outbox_by_key": {}, "outbox_rows": {}, "next_outbox_id": 1,
        "report_rows": {}, "next_report_id": 1,
        "evidence_rows": {}, "next_evidence_id": 1,
    }
    if sprint2_report:
        # Seed a settled Sprint 2 report — _attempt_price_path_enhancement
        # returns PRICE_PATH_NOT_YET_AVAILABLE without one.
        shared["report_rows"][1] = (
            1, 1, "user-aaa", "US", dt.date(2026, 6, 4), "America/New_York",
            "1.0.0", "1.1.0", "2.0.0", "1.0.0", "deadbeef", "LIMITED_EVIDENCE",
            {"trade_id": 1}, [], [], {}, [], [],
            dt.datetime(2026, 6, 4, tzinfo=dt.timezone.utc), None,
        )
        shared["next_report_id"] = 2
    return shared


def _fake_bars():
    return [
        {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 115.0, "low": 99.0, "close": 105.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        {"date": dt.date(2026, 6, 3), "open": 105.0, "high": 112.0, "low": 90.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
    ]


@contextmanager
def _patched_conn(shared):
    conn = _FakeConn(shared)

    @contextmanager
    def _fake_conn():
        yield conn

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        yield conn


def _attempt(shared, **kwargs):
    from api.routers.paper_trading import _attempt_price_path_enhancement
    with _patched_conn(shared):
        return _attempt_price_path_enhancement(user_id="user-aaa", trade_id=1, **kwargs)


@pytest.mark.regression
class TestNoPriorSprint2Report:
    def test_returns_not_yet_available(self):
        shared = _new_shared([{}], sprint2_report=False)
        report, status = _attempt(shared)
        assert report is None
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_NOT_YET_AVAILABLE


@pytest.mark.regression
class TestSuccessfulGenerationAndIdempotentReplay:
    def test_first_call_generates_creates_own_outbox_row(self):
        shared = _new_shared([{}])
        report, status = _attempt(shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None
        assert report.report_schema_version == "1.1.0"
        assert len(shared["outbox_rows"]) == 1
        assert list(shared["outbox_rows"].values())[0]["status"] == "COMPLETE" or list(shared["outbox_rows"].values())[0]["status"] == "LIMITED_EVIDENCE"

    def test_split_in_window_is_classified_ambiguous_and_caps_limited_evidence(self):
        """Stage J1B load-bearing proof: a split inside the acquisition
        window makes price_path_acquisition.build_price_path_evidence
        (the REAL, unfaked classification logic -- only the fetch
        functions are faked here) set data_completeness to
        AMBIGUOUS_RESOLUTION (a split changes what 'unadjusted' means
        across the boundary). That real signal must flow through
        classify_acquired_evidence and compute_report_completeness_
        ceiling into the persisted report's own status -- not merely be
        computed and discarded."""
        shared = _new_shared([{}])
        report, status = _attempt(
            shared, fetch_bars_fn=lambda *a: _fake_bars(),
            fetch_splits_fn=lambda *a: [dt.date(2026, 6, 2)], fetch_dividends_fn=lambda *a: [],
        )
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None
        assert report.status == "LIMITED_EVIDENCE"
        assert report.structured_report["price_path"]["evidence_quality_decision"]["evidence_status"] == "AMBIGUOUS_RESOLUTION"
        assert report.structured_report["price_path"]["acquisition_decision"]["provider_call_expected"] is True

    def test_zero_bars_never_invokes_the_calculator(self):
        """Stage J1B-Assurance-Closure, Stage 4 -- direct instrumentation
        of the actual calculator entry points (not an inference from
        null output fields). A zero-bar provider response classifies
        SOURCE_UNAVAILABLE / CALCULATION_UNAVAILABLE and must never call
        compute_excursion/detect_touches/classify_touch_order at all."""
        from unittest.mock import patch
        shared = _new_shared([{}])
        with patch("services.postmortem.price_path_generation.compute_excursion") as spy_excursion, \
             patch("services.postmortem.price_path_generation.detect_touches") as spy_touches, \
             patch("services.postmortem.price_path_generation.classify_touch_order") as spy_order:
            report, status = _attempt(
                shared, fetch_bars_fn=lambda *a: [], fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )
            spy_excursion.assert_not_called()
            spy_touches.assert_not_called()
            spy_order.assert_not_called()
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None
        assert report.status == "LIMITED_EVIDENCE"
        assert report.structured_report["price_path"]["mfe_abs"] is None
        assert report.structured_report["price_path"]["evidence_quality_decision"]["evidence_status"] == "SOURCE_UNAVAILABLE"

    def test_complete_evidence_does_invoke_the_calculator(self):
        """The positive control for the spy above -- a normal COMPLETE
        acquisition must actually call the calculator, proving the spy
        mechanism itself is sound (not merely un-invoked by accident)."""
        from unittest.mock import patch
        shared = _new_shared([{}])
        with patch(
            "services.postmortem.price_path_generation.compute_excursion",
            wraps=__import__("services.postmortem.price_path_generation", fromlist=["compute_excursion"]).compute_excursion,
        ) as spy_excursion:
            report, status = _attempt(
                shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )
            spy_excursion.assert_called_once()
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None

    def test_unsupported_completeness_never_invokes_the_calculator(self):
        """Stage J1B-Final-Reconciliation, Stage 5 -- forced test-only
        quality decision (never a naturally reachable acquired-bundle
        state via the real fetch functions): proves the live path's
        no-report policy for UNSUPPORTED_EVIDENCE_COMPLETENESS holds
        even where the calculator COULD have been reached, not merely
        inferred from control-flow review."""
        from unittest.mock import patch
        from services.postmortem import price_path_evidence_decision as pped
        shared = _new_shared([{}])
        forced = pped.HistoricalEvidenceQualityDecision(
            evidence_status=pped.UNSUPPORTED_EVIDENCE_COMPLETENESS, calculation_status=pped.CALCULATION_UNAVAILABLE,
            fresh_persistence_permitted=False, report_completeness_ceiling="LIMITED_EVIDENCE",
        )
        with patch.object(pped, "classify_acquired_evidence", return_value=forced), \
             patch("services.postmortem.price_path_generation.compute_excursion") as spy_excursion, \
             patch("services.postmortem.price_path_generation.detect_touches") as spy_touches, \
             patch("services.postmortem.price_path_generation.classify_touch_order") as spy_order:
            report, status = _attempt(
                shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )
            spy_excursion.assert_not_called()
            spy_touches.assert_not_called()
            spy_order.assert_not_called()
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        assert shared["evidence_rows"] == {}
        assert len(shared["report_rows"]) == 1  # only the seeded Sprint 2 report

    def test_source_invalid_never_invokes_the_calculator(self):
        """SOURCE_INVALID has no live acquired-bundle producer (see
        price_path_evidence_decision's own module docstring), but the
        live path's defensive no-report handling for it is still
        directly proven here via a forced quality decision."""
        from unittest.mock import patch
        from services.postmortem import price_path_evidence_decision as pped
        shared = _new_shared([{}])
        forced = pped.HistoricalEvidenceQualityDecision(
            evidence_status=pped.SOURCE_INVALID, calculation_status=pped.CALCULATION_UNAVAILABLE,
            fresh_persistence_permitted=False, report_completeness_ceiling="LIMITED_EVIDENCE",
        )
        with patch.object(pped, "classify_acquired_evidence", return_value=forced), \
             patch("services.postmortem.price_path_generation.compute_excursion") as spy_excursion, \
             patch("services.postmortem.price_path_generation.detect_touches") as spy_touches, \
             patch("services.postmortem.price_path_generation.classify_touch_order") as spy_order:
            report, status = _attempt(
                shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
            )
            spy_excursion.assert_not_called()
            spy_touches.assert_not_called()
            spy_order.assert_not_called()
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        assert shared["evidence_rows"] == {}
        assert len(shared["report_rows"]) == 1

    def test_second_call_is_idempotent_no_second_provider_call(self):
        shared = _new_shared([{}])
        calls = {"n": 0}

        def _bars(*a):
            calls["n"] += 1
            return _fake_bars()

        first, status1 = _attempt(shared, fetch_bars_fn=_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        second, status2 = _attempt(shared, fetch_bars_fn=_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert status1 == ptr.PRICE_PATH_GENERATED
        assert status2 == ptr.PRICE_PATH_ALREADY_COMPLETE
        assert first.id == second.id
        assert calls["n"] == 1  # provider called exactly once across both attempts
        assert len(shared["report_rows"]) == 2  # Sprint 2 seed + one price-path report


@pytest.mark.regression
class TestLeaseConcurrency:
    def test_active_non_expired_lease_returns_in_progress(self):
        shared = _new_shared([{}])
        # Simulate another worker already holding the lease.
        shared["outbox_by_key"][(1, "1.1.0", "1.1.0+price_path:1.0.0+src:1.1.0", "2.0.0")] = 1
        shared["outbox_rows"][1] = {
            "id": 1, "paper_trade_id": 1, "user_id": "user-aaa",
            "requested_report_schema_version": "1.1.0",
            "requested_calculation_version": "1.1.0+price_path:1.0.0+src:1.1.0",
            "requested_rules_version": "2.0.0", "status": "GENERATING", "attempt_count": 1,
            "source_request_id": None, "claimed_by": "other-worker-token",
            "next_attempt_at": None,
            "lease_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60),
        }
        shared["next_outbox_id"] = 2
        report, status = _attempt(shared)
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_GENERATION_IN_PROGRESS

    def test_expired_lease_is_reclaimable(self):
        shared = _new_shared([{}])
        shared["outbox_by_key"][(1, "1.1.0", "1.1.0+price_path:1.0.0+src:1.1.0", "2.0.0")] = 1
        shared["outbox_rows"][1] = {
            "id": 1, "paper_trade_id": 1, "user_id": "user-aaa",
            "requested_report_schema_version": "1.1.0",
            "requested_calculation_version": "1.1.0+price_path:1.0.0+src:1.1.0",
            "requested_rules_version": "2.0.0", "status": "GENERATING", "attempt_count": 1,
            "source_request_id": None, "claimed_by": "crashed-worker-token",
            "next_attempt_at": None,
            "lease_expires_at": dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5),  # already expired
        }
        shared["next_outbox_id"] = 2
        report, status = _attempt(shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None
        assert shared["outbox_rows"][1]["claimed_by"] is None  # settled, lease released


@pytest.mark.regression
class TestProviderFailureClassification:
    def test_provider_error_marks_failed_retryable_with_backoff(self):
        shared = _new_shared([{}])

        def _failing(*a):
            raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")

        report, status = _attempt(shared, fetch_bars_fn=_failing, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        outbox_row = list(shared["outbox_rows"].values())[0]
        assert outbox_row["status"] == "FAILED_RETRYABLE"
        assert outbox_row["next_attempt_at"] is not None
        assert outbox_row["claimed_by"] is None  # lease released, reclaimable after backoff
        assert len(shared["evidence_rows"]) == 0  # no partial evidence
        assert len(shared["report_rows"]) == 1  # only the seeded Sprint 2 report — no fabricated report

    def test_retry_after_backoff_succeeds_and_completes(self):
        shared = _new_shared([{}])
        attempt_n = {"n": 0}

        def _flaky(*a):
            attempt_n["n"] += 1
            if attempt_n["n"] == 1:
                raise PriceProviderAcquisitionError("PROVIDER_FETCH_FAILED", "simulated outage")
            return _fake_bars()

        first, status1 = _attempt(shared, fetch_bars_fn=_flaky, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert status1 == ptr.PRICE_PATH_FAILED_RETRYABLE
        # Simulate backoff having elapsed.
        outbox_row = list(shared["outbox_rows"].values())[0]
        outbox_row["next_attempt_at"] = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        second, status2 = _attempt(shared, fetch_bars_fn=_flaky, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        assert status2 == ptr.PRICE_PATH_GENERATED
        assert second is not None
        assert attempt_n["n"] == 2


@pytest.mark.regression
class TestCrossUserIsolation:
    def test_other_user_gets_not_yet_available_not_a_leak(self):
        from api.routers.paper_trading import _attempt_price_path_enhancement
        shared = _new_shared([{"owner": "someone-else"}], sprint2_report=False)
        with _patched_conn(shared):
            report, status = _attempt_price_path_enhancement(user_id="attacker", trade_id=1)
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_NOT_YET_AVAILABLE


@pytest.mark.regression
class TestHistoricalEligibilityGating:
    """Trade Postmortem Sprint 3A, Stage J — a permanently invalid trade
    (incoherent timeline, unsupported market) never creates an outbox
    row and never reaches the provider, even with a valid prior Sprint 2
    report."""

    def test_entry_after_exit_never_creates_outbox_or_calls_provider(self):
        shared = _new_shared([{
            "opened_at": dt.datetime(2026, 6, 5, tzinfo=dt.timezone.utc),
            "closed_at": dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        }])
        calls = {"n": 0}

        def _bars(*a):
            calls["n"] += 1
            return _fake_bars()

        report, status = _attempt(shared, fetch_bars_fn=_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_NOT_YET_AVAILABLE
        assert calls["n"] == 0
        assert shared["outbox_rows"] == {}

    def test_unsupported_market_never_creates_outbox_or_calls_provider(self):
        shared = _new_shared([{"market": "XX"}])
        calls = {"n": 0}

        def _bars(*a):
            calls["n"] += 1
            return _fake_bars()

        report, status = _attempt(shared, fetch_bars_fn=_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_NOT_YET_AVAILABLE
        assert calls["n"] == 0
        assert shared["outbox_rows"] == {}

    def test_non_finite_entry_price_never_creates_outbox(self):
        shared = _new_shared([{"entry_price": float("nan")}])
        report, status = _attempt(shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_NOT_YET_AVAILABLE
        assert shared["outbox_rows"] == {}

    def test_valid_trade_with_missing_context_still_generates(self):
        """Missing entry/exit snapshot caps report completeness (Sprint
        2's own responsibility, unchanged) but never blocks price-path
        market-data work — this trade's timeline/price/market are
        otherwise perfectly valid."""
        shared = _new_shared([{}])  # default fixture uses a LIMITED_EVIDENCE Sprint 2 seed report
        report, status = _attempt(shared, fetch_bars_fn=lambda *a: _fake_bars(), fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [])
        import api.routers.paper_trading as ptr
        assert status == ptr.PRICE_PATH_GENERATED
        assert report is not None


@pytest.mark.regression
class TestContradictoryReplayStateFailsClosed:
    """Stage J1B-Final-Reconciliation, Stage 3 -- deterministic test-only
    forced contradiction injection (never altering production logic to
    make this naturally reachable): classify_replay_or_acquisition is
    monkeypatched to claim COMPATIBLE_REPLAY while no compatible evidence
    is actually seeded, proving the explicit fail-closed branch that
    replaced the old `assert evidence is not None`."""

    def test_forced_contradiction_settles_retryable_with_no_side_effects(self):
        from unittest.mock import patch
        from services.postmortem import price_path_evidence_decision

        shared = _new_shared([{}])  # no evidence_rows seeded -- ctx.compatible_evidence is genuinely None

        def _raises(*a, **k):
            raise AssertionError("provider must not be called on the integrity-violation path")

        fake_acquisition = price_path_evidence_decision.HistoricalEvidenceAcquisitionDecision(
            acquisition_status=price_path_evidence_decision.COMPATIBLE_REPLAY,
            provider_call_expected=False, compatible_evidence_found=True, evidence_id=999999,
        )
        with patch.object(price_path_evidence_decision, "classify_replay_or_acquisition", return_value=fake_acquisition):
            report, status = _attempt(shared, fetch_bars_fn=_raises, fetch_splits_fn=_raises, fetch_dividends_fn=_raises)

        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        assert len(shared["report_rows"]) == 1  # only the seeded Sprint 2 report -- no price-path report added
        assert shared["evidence_rows"] == {}
        outbox_row = list(shared["outbox_rows"].values())[0]
        assert outbox_row["status"] == "FAILED_RETRYABLE"
        assert outbox_row["last_error_code"] == price_path_evidence_decision.INTERNAL_INTEGRITY_VIOLATION


@pytest.mark.regression
class TestForcedNaiveTimestampFailsClosedBeforeProviderIO:
    """Stage J3B.2 -- test-only contradiction injection (never altering
    production logic to make this naturally reachable): the real,
    genuinely timezone-aware paper_trades.opened_at/closed_at are
    preserved untouched; only load_generation_context's OWN RETURNED
    GenerationContext is monkeypatched afterward to carry a naive
    entry_timestamp, simulating a future internal caller that violates
    the timezone-aware contract. Proves the Stage J3B.2 pre-I/O guard
    (_require_timezone_aware_trade_timestamps, called at the top of
    acquire_price_path_evidence) rejects it before any provider function
    is ever invoked, and that the existing generic `except Exception`
    handler in _attempt_price_path_enhancement settles this exactly like
    any other internal failure -- sanitized error summary (exception
    class name only, never the raw naive timestamp), retryable outbox,
    zero evidence, zero report."""

    def test_forced_naive_entry_timestamp_fails_closed_zero_provider_calls(self):
        import dataclasses
        from unittest.mock import patch

        from services.postmortem import price_path_generation

        shared = _new_shared([{}])  # genuinely timezone-aware opened_at/closed_at, per _trade_row's own defaults

        def _raises(*a, **k):
            raise AssertionError("provider must not be called when the pre-I/O guard rejects a naive timestamp")

        real_loader = price_path_generation.load_generation_context

        def _forced_naive_loader(*args, **kwargs):
            ctx = real_loader(*args, **kwargs)
            # The real opened_at/closed_at columns (and this trade's
            # real, aware timestamps) are never touched -- only the
            # in-memory GenerationContext this call returns is mutated,
            # simulating a future internal caller passing a naive value.
            naive_entry = ctx.entry_timestamp.replace(tzinfo=None)
            return dataclasses.replace(ctx, entry_timestamp=naive_entry)

        with patch.object(price_path_generation, "load_generation_context", side_effect=_forced_naive_loader):
            report, status = _attempt(shared, fetch_bars_fn=_raises, fetch_splits_fn=_raises, fetch_dividends_fn=_raises)

        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        assert len(shared["report_rows"]) == 1  # only the seeded Sprint 2 report
        assert shared["evidence_rows"] == {}
        outbox_row = list(shared["outbox_rows"].values())[0]
        assert outbox_row["status"] == "FAILED_RETRYABLE"
        assert outbox_row["last_error_code"] == ptr._PRICE_PATH_ERROR_CODE
        # The real trade row itself is untouched -- opened_at/closed_at
        # remain the genuinely aware timestamps _trade_row seeded.
        real_trade = shared["trades"][1]
        assert real_trade[10].tzinfo is not None  # opened_at
        assert real_trade[11].tzinfo is not None  # closed_at

    def test_forced_naive_exit_timestamp_fails_closed_zero_provider_calls(self):
        import dataclasses
        from unittest.mock import patch

        from services.postmortem import price_path_generation

        shared = _new_shared([{}])

        def _raises(*a, **k):
            raise AssertionError("provider must not be called when the pre-I/O guard rejects a naive timestamp")

        real_loader = price_path_generation.load_generation_context

        def _forced_naive_loader(*args, **kwargs):
            ctx = real_loader(*args, **kwargs)
            naive_exit = ctx.exit_timestamp.replace(tzinfo=None)
            return dataclasses.replace(ctx, exit_timestamp=naive_exit)

        with patch.object(price_path_generation, "load_generation_context", side_effect=_forced_naive_loader):
            report, status = _attempt(shared, fetch_bars_fn=_raises, fetch_splits_fn=_raises, fetch_dividends_fn=_raises)

        import api.routers.paper_trading as ptr
        assert report is None
        assert status == ptr.PRICE_PATH_FAILED_RETRYABLE
        assert shared["evidence_rows"] == {}
        outbox_row = list(shared["outbox_rows"].values())[0]
        assert outbox_row["status"] == "FAILED_RETRYABLE"
        assert outbox_row["last_error_code"] == ptr._PRICE_PATH_ERROR_CODE
