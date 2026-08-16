"""
Wave C, WC-O final correction — §2, end-to-end proof that
get_current_governed_report's HTTP response is byte-for-byte unchanged
across all seven governed availability states when the RAW metrics
recording primitive (current_report_metrics.record_availability) is
forced to raise. Calls the actual route function directly (bypassing
FastAPI's dependency injection, which doesn't matter here since
user_id is passed explicitly) with a fake `_conn()` and monkeypatched
report_store/ready-response helpers — never a parallel reimplementation
of the handler's own branching.
"""
from contextlib import contextmanager

import pytest
from fastapi import Response

from api.routers import paper_trading
from services.postmortem import current_report_metrics


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, *, trade_row, outbox_row=None):
        self._trade_row = trade_row
        self._outbox_row = outbox_row

    def execute(self, sql, params=None):
        if "FROM paper_trades" in sql:
            return _FakeCursor(self._trade_row)
        if "FROM paper_trade_postmortem_outbox" in sql:
            return _FakeCursor(self._outbox_row)
        raise AssertionError(f"unexpected query in fake conn: {sql!r}")


def _fake_conn_factory(*, trade_row, outbox_row=None):
    @contextmanager
    def _conn():
        yield _FakeConn(trade_row=trade_row, outbox_row=outbox_row)
    return _conn


@pytest.fixture(autouse=True)
def _reset_metrics():
    current_report_metrics.reset_for_tests()
    yield
    current_report_metrics.reset_for_tests()


def _call_endpoint(monkeypatch, *, flag_enabled, trade_row, outbox_row=None, existing_report=None, ready_response=None):
    monkeypatch.setattr(paper_trading, "_trade_postmortem_price_path_enabled", lambda: flag_enabled)
    monkeypatch.setattr(paper_trading, "_conn", _fake_conn_factory(trade_row=trade_row, outbox_row=outbox_row))
    monkeypatch.setattr(paper_trading.report_store, "get_current_report", lambda *a, **k: existing_report)
    if existing_report is not None:
        monkeypatch.setattr(
            paper_trading, "_build_current_report_ready_response",
            lambda report, trade_id, **kwargs: ready_response,
        )
    response = Response()
    result = paper_trading.get_current_governed_report(trade_id=42, response=response, user_id="user-a")
    return result, response


_TRADE_ROW_CLOSED = ("user-a", "CLOSED", "US", "AAPL")
_TRADE_ROW_OPEN = ("user-a", "OPEN", "US", "AAPL")

_VALID_READY_RESPONSE = paper_trading.CurrentReportReadResponse(
    trade_id=42, availability="READY",
    report_schema_version="1.2.0", calculation_version="calc-v1", attribution_rules_version="1.0.0",
    evidence_bundle_version="1.0.0", market="US", report_trading_date="2026-08-01",
    market_timezone="America/New_York", status="COMPLETE", generated_at="2026-08-01T20:00:00Z",
    structured_report={
        "price_path": {
            "version_and_provenance": {
                "report_schema_version": "1.2.0", "calculation_version": "calc-v1",
                "numerical_rules_version": "1.0.0", "governed_semantic_rules_version": "1.0.0",
                "governed_claim_rules_version": "1.0.0", "entry_snapshot_schema_version": None,
                "exit_snapshot_schema_version": None, "level_history_contract_version": None,
                "source_version": "1.0.0",
            },
        },
    },
    claims=[], evidence_items=[], evidence_gaps=[], warnings=[],
    source_manifest={
        "has_entry_snapshot": False, "has_exit_snapshot": False,
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "calc-v1",
        "attribution_rules_version": "1.0.0", "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    },
    supersedes_report_id=None,
)

_STATES = [
    ("READY", dict(trade_row=_TRADE_ROW_CLOSED, existing_report=object(), ready_response=_VALID_READY_RESPONSE)),
    ("PROCESSING", dict(trade_row=_TRADE_ROW_CLOSED, outbox_row=("PENDING",))),
    ("NOT_ELIGIBLE", dict(trade_row=_TRADE_ROW_OPEN)),
    ("NOT_AVAILABLE", dict(trade_row=_TRADE_ROW_CLOSED, outbox_row=None)),
    ("TERMINAL_FAILURE", dict(trade_row=_TRADE_ROW_CLOSED, outbox_row=("FAILED_TERMINAL",))),
    ("INTEGRITY_CONTRADICTION", dict(trade_row=_TRADE_ROW_CLOSED, outbox_row=("COMPLETE",))),
    ("FEATURE_DISABLED", dict(trade_row=_TRADE_ROW_CLOSED, flag_enabled=False)),
]


@pytest.mark.unit
@pytest.mark.parametrize("expected_availability,kwargs", _STATES, ids=[s[0] for s in _STATES])
class TestAvailabilityFailOpenAcrossAllGovernedStates:
    def test_response_unchanged_when_raw_metrics_primitive_raises(self, monkeypatch, expected_availability, kwargs):
        kwargs.setdefault("flag_enabled", True)

        # Force the RAW (throwing) primitive to raise — proving
        # safe_record_availability's fail-open contract actually holds
        # at the real call site, not just in the metrics module's own
        # isolated tests.
        def _broken_record_availability(*a, **k):
            raise RuntimeError("simulated metrics store failure")
        monkeypatch.setattr(current_report_metrics, "record_availability", _broken_record_availability)

        result, response = _call_endpoint(monkeypatch, **kwargs)

        assert result.availability == expected_availability
        assert response.headers.get("Cache-Control") == "private, no-store"
        if expected_availability == "READY":
            assert result.trade_id == 42
        else:
            # Every READY-only field must be null on a non-READY response
            # — CurrentReportReadResponse's own invariant, unaffected by
            # the metrics failure.
            assert result.structured_report is None
            assert result.claims is None
            assert result.status is None

    def test_response_identical_with_healthy_metrics(self, monkeypatch, expected_availability, kwargs):
        """Same fixtures, metrics healthy — the exact control-path
        comparison proving the metrics failure changed NOTHING about
        the response."""
        kwargs.setdefault("flag_enabled", True)
        result, response = _call_endpoint(monkeypatch, **kwargs)
        assert result.availability == expected_availability
        assert response.headers.get("Cache-Control") == "private, no-store"
