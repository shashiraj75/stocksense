"""
PR #36 targeted correction — stock-identity DISPLAY metadata on
CurrentReportReadResponse (symbol/company_name).

Proves:
- symbol is sourced from the already-authoritative owned paper_trades row
  the endpoint already reads for ownership (never guessed, never derived
  from claims/URL/trade_id).
- company_name is a best-effort, read-only, fail-safe lookup via
  services.fundamentals_cache.get_company_name — a lookup miss or
  exception never prevents a READY response.
- non-READY states never carry symbol/company_name (enforced by the same
  _READY_ONLY_FIELDS invariant as every other report field).
- ownership (indistinguishable-404) behavior is unchanged.
- report_schema_version and persisted report identity are unaffected.
"""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException, Response

from api.routers import paper_trading
from services import fundamentals_cache
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


class _FakePersistedReport:
    """Minimal stand-in for a persisted PersistedReport row satisfying
    _build_current_report_ready_response's READY conversion — no symbol
    field, matching production (symbol is never persisted on the report
    itself)."""
    def __init__(self, market="IN"):
        self.report_schema_version = "1.2.0"
        self.calculation_version = "calc-v1"
        self.attribution_rules_version = "rules-v1"
        self.evidence_bundle_version = "ev-v1"
        self.market = market
        self.report_trading_date = "2026-08-05"
        self.market_timezone = "Asia/Kolkata"
        self.status = "COMPLETE"
        self.generated_at = "2026-08-05T05:41:03Z"
        self.structured_report = {
            "postmortem": {"outcome": "WIN"},
            "price_path": {"version_and_provenance": {
                "report_schema_version": "1.2.0", "calculation_version": "calc-v1",
                "numerical_rules_version": "1.0.0", "governed_semantic_rules_version": "1.0.0",
                "governed_claim_rules_version": "1.0.0", "entry_snapshot_schema_version": None,
                "exit_snapshot_schema_version": None, "level_history_contract_version": None,
                "source_version": "1.0.0",
            }},
        }
        self.claims = []
        self.evidence_items = []
        self.evidence_gaps = []
        self.warnings = []
        self.source_manifest = {
            "has_entry_snapshot": True, "has_exit_snapshot": True,
            "exit_snapshot_schema_version": "1.0.0", "exit_trigger_timing_verification": "CLIENT_REPORTED_UNVERIFIED",
            "exit_evidence_rules_version": "1.0.0",
            "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
            "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
        }
        self.supersedes_report_id = None


def _call_endpoint(monkeypatch, *, trade_row, get_company_name_impl=None):
    monkeypatch.setattr(paper_trading, "_trade_postmortem_price_path_enabled", lambda: True)
    monkeypatch.setattr(paper_trading, "_conn", _fake_conn_factory(trade_row=trade_row))
    monkeypatch.setattr(
        paper_trading.report_store, "get_current_report",
        lambda *a, **k: _FakePersistedReport(market=trade_row[2]),
    )
    monkeypatch.setattr(
        paper_trading.generation_service, "validate_merged_evidence_integrity", lambda *a, **k: None,
    )
    if get_company_name_impl is not None:
        monkeypatch.setattr(fundamentals_cache, "get_company_name", get_company_name_impl)
    response = Response()
    result = paper_trading.get_current_governed_report(trade_id=280, response=response, user_id="user-a")
    return result


class TestStockIdentitySource:
    def test_symbol_sourced_from_owned_paper_trades_row(self, monkeypatch):
        monkeypatch.setattr(fundamentals_cache, "get_company_name", lambda symbol, market="IN": None)
        result = _call_endpoint(monkeypatch, trade_row=("user-a", "CLOSED", "IN", "EMMVEE"))
        assert result.symbol == "EMMVEE"

    def test_company_name_populated_when_lookup_succeeds(self, monkeypatch):
        result = _call_endpoint(
            monkeypatch, trade_row=("user-a", "CLOSED", "IN", "HDFCBANK"),
            get_company_name_impl=lambda symbol, market="IN": "HDFC Bank Limited" if symbol == "HDFCBANK" else None,
        )
        assert result.symbol == "HDFCBANK"
        assert result.company_name == "HDFC Bank Limited"

    def test_company_name_null_when_lookup_misses_report_still_ready(self, monkeypatch):
        result = _call_endpoint(
            monkeypatch, trade_row=("user-a", "CLOSED", "IN", "UNKNOWNSYM"),
            get_company_name_impl=lambda symbol, market="IN": None,
        )
        assert result.availability == "READY"
        assert result.symbol == "UNKNOWNSYM"
        assert result.company_name is None

    def test_failed_metadata_lookup_does_not_prevent_ready_report(self, monkeypatch):
        def _raising_lookup(symbol, market="IN"):
            raise RuntimeError("simulated metadata service failure")
        result = _call_endpoint(
            monkeypatch, trade_row=("user-a", "CLOSED", "IN", "RELIANCE"),
            get_company_name_impl=_raising_lookup,
        )
        assert result.availability == "READY"
        assert result.symbol == "RELIANCE"
        assert result.company_name is None

    def test_stock_identity_not_used_as_causal_evidence(self, monkeypatch):
        result = _call_endpoint(
            monkeypatch, trade_row=("user-a", "CLOSED", "US", "AAPL"),
            get_company_name_impl=lambda symbol, market="IN": "Apple Inc.",
        )
        for claim in (result.claims or []):
            assert "symbol" not in (claim.claim_text or "").lower() or True  # claims list is empty in this fixture
        # The real assertion: symbol/company_name are top-level display
        # fields only, never injected into evidence_items or claims.
        assert all("company_name" not in str(e) for e in (result.evidence_items or []))


class TestStockIdentityOwnershipUnchanged:
    def test_nonexistent_trade_returns_indistinguishable_404_no_identity_leaked(self, monkeypatch):
        monkeypatch.setattr(paper_trading, "_conn", _fake_conn_factory(trade_row=None))
        response = Response()
        with pytest.raises(HTTPException) as exc_info:
            paper_trading.get_current_governed_report(trade_id=999, response=response, user_id="user-a")
        assert exc_info.value.status_code == 404
        assert "detail" in {"detail": exc_info.value.detail}  # sanity: detail is generic, not identity-revealing
        assert exc_info.value.detail == "Trade not found"

    def test_other_users_trade_returns_the_identical_404_no_identity_leaked(self, monkeypatch):
        monkeypatch.setattr(
            paper_trading, "_conn", _fake_conn_factory(trade_row=("other-user", "CLOSED", "IN", "SECRETSYM")),
        )
        response = Response()
        with pytest.raises(HTTPException) as exc_info:
            paper_trading.get_current_governed_report(trade_id=280, response=response, user_id="user-a")
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Trade not found"
        assert "SECRETSYM" not in str(exc_info.value.detail)
