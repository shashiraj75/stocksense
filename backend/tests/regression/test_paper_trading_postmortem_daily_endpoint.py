"""
Trade Postmortem Engine, Stage 3 (Sprint 1 evidence-provenance rewrite) —
regression tests for GET /api/paper-trading/postmortem/daily.

Follows the exact fake-connection mocking pattern established in
test_paper_trading_postmortem_endpoint.py: no real database, no network.
"""
import datetime as dt
import time
from contextlib import contextmanager
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


@pytest.fixture(autouse=True)
def _feature_enabled(monkeypatch):
    """PR #32 pre-merge correction: the daily endpoint is disabled by
    default (TRADE_POSTMORTEM_DAILY_ENABLED unset). This file's existing
    tests exercise the endpoint's functional behavior, so they need the
    flag explicitly enabled — see test_trade_postmortem_daily_feature_flag.py
    for the flag-parsing unit tests, and TestFeatureFlagGating below in
    this file for the disabled-state regression coverage."""
    monkeypatch.setenv("TRADE_POSTMORTEM_DAILY_ENABLED", "true")


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _token(sub: str = "user-aaa") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


class _RecordingConn:
    """Supports both `.fetchall()` (the daily trade-list query) and
    `.fetchone()` (the per-trade entry-snapshot lookup) against the same
    patched connection."""

    def __init__(self, main_rows, snapshot_rows_by_trade_id=None):
        self.calls = []
        self._main_rows = list(main_rows)
        self._snapshot_rows = snapshot_rows_by_trade_id or {}
        self._last_params = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last_params = params
        return self

    def fetchall(self):
        return list(self._main_rows)

    def fetchone(self):
        trade_id = self._last_params[0] if self._last_params else None
        return self._snapshot_rows.get(trade_id)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _row(
    *,
    trade_id=1,
    owner="user-aaa",
    status="CLOSED",
    symbol="AAPL",
    market="US",
    quantity=10,
    entry_price=100.0,
    exit_price=120.0,
    stop_loss=90.0,
    target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    closed_at=dt.datetime(2026, 6, 2, 15, 0, tzinfo=dt.timezone.utc),
    trade_management_mode="manual",
    exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


def _get_daily(client, params, main_rows, snapshot_rows=None, auth_sub="user-aaa"):
    recorder = _RecordingConn(main_rows=main_rows, snapshot_rows_by_trade_id=snapshot_rows)

    @contextmanager
    def _fake_conn():
        yield recorder

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        resp = client.get("/api/paper-trading/postmortem/daily", params=params, headers=_auth(auth_sub))
    return resp, recorder


@pytest.mark.regression
class TestDailyPostmortemEndpoint:
    def test_multi_trade_day_returns_200_with_evidence_attribution(self, client):
        rows = [
            _row(trade_id=1, exit_price=120.0, closed_at=dt.datetime(2026, 6, 2, 15, 0, tzinfo=dt.timezone.utc)),
            _row(trade_id=2, exit_price=80.0, closed_at=dt.datetime(2026, 6, 2, 16, 0, tzinfo=dt.timezone.utc)),
        ]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == "2.0.0"
        assert body["summary"]["trade_count"] == 2
        assert body["summary"]["win_count"] == 1
        assert body["summary"]["loss_count"] == 1
        assert len(body["trades"]) == 2
        for t in body["trades"]:
            attribution = t["attribution"]
            assert "claims" in attribution
            assert "signal_scorecard" in attribution
            assert "contributor_assessments" in attribution
            assert "primary_contributor" in attribution
            assert "thesis_verdict" in attribution
            # Every claim, per Sprint 1 contract, must have a rule ID.
            for claim in attribution["claims"]:
                assert claim["rule_id"]
                assert claim["rule_version"]

    def test_response_no_longer_has_narrative_or_root_cause_fields(self, client):
        rows = [_row(trade_id=1, exit_price=120.0)]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        body = resp.json()
        t = body["trades"][0]
        assert "narrative" not in t
        assert "root_cause_breakdown" not in body["summary"]

    def test_empty_day_returns_200_not_404(self, client):
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [])
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["trade_count"] == 0
        assert body["trades"] == []

    def test_end_to_end_serialization_values_not_just_key_presence(self, client):
        """Sprint 1 independent review, Stage 14 — asserts actual VALUES
        through the full router -> Pydantic -> JSON path, not just that the
        expected keys exist (a mocked-happy-path test can pass that check
        even if production wiring silently drops real content)."""
        rows = [_row(trade_id=1, exit_price=95.0, stop_loss=10.0, target_price=101.0, exit_reason="MANUAL")]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        assert resp.status_code == 200
        t = resp.json()["trades"][0]
        attribution = t["attribution"]

        # evidence_items: at least one real item with a deterministic,
        # trade-scoped ID (the POSITION_MANAGEMENT rule fires for this
        # exact manual-loss-closer-to-target fixture).
        assert len(attribution["evidence_items"]) >= 1
        assert any(e["evidence_id"].startswith("EV-1-") for e in attribution["evidence_items"])

        # claims: real content, not placeholders, and every evidence
        # reference resolves within this same response.
        known_evidence_ids = {e["evidence_id"] for e in attribution["evidence_items"]}
        known_claim_ids = {c["claim_id"] for c in attribution["claims"]}
        assert len(attribution["claims"]) > 5
        insufficient_count = 0
        for claim in attribution["claims"]:
            for eid in claim["supporting_evidence_ids"] + claim["opposing_evidence_ids"]:
                assert eid in known_evidence_ids, f"dangling evidence ref {eid} in {claim['claim_id']}"
            if claim["evidence_class"] == "INSUFFICIENT_EVIDENCE":
                insufficient_count += 1
                assert claim["claim_text"] == "Insufficient evidence to determine this factor reliably."
                assert claim["confidence_band"] == "NOT_ASSESSABLE"
        assert insufficient_count > 0  # this fixture has no entry snapshot — most claims are honest gaps

        # contributor_assessments: exactly the 11 known categories, and
        # POSITION_MANAGEMENT specifically SUPPORTED for this fixture.
        categories = {c["category"] for c in attribution["contributor_assessments"]}
        assert categories == {
            "STOCK_SELECTION", "ENTRY_TIMING", "POSITION_MANAGEMENT", "EXIT_LOGIC", "MARKET_CONDITIONS",
            "SECTOR_CONDITIONS", "VOLATILITY", "LIQUIDITY", "NEWS_OR_EVENT", "PRICE_NOISE", "ADMINISTRATIVE_ACTION",
        }
        pm = next(c for c in attribution["contributor_assessments"] if c["category"] == "POSITION_MANAGEMENT")
        assert pm["support_level"] == "SUPPORTED"
        assert pm["claim_id"] in known_claim_ids

        # thesis_verdict: no snapshot -> UNSUPPORTED, with a real backing claim.
        assert attribution["thesis_verdict"] == "UNSUPPORTED"
        assert attribution["thesis_verdict_claim_id"] in known_claim_ids

        # primary_contributor: null (SUPPORTED alone never clears the
        # STRONGLY_SUPPORTED bar), with a claim explaining why, using the
        # exact fallback sentence.
        assert attribution["primary_contributor"] is None
        assert attribution["primary_contributor_claim_id"] in known_claim_ids
        primary_claim = next(c for c in attribution["claims"] if c["claim_id"] == attribution["primary_contributor_claim_id"])
        assert primary_claim["claim_text"] == "Insufficient evidence to determine this factor reliably."

        # daily summary reflects this one trade correctly.
        summary = resp.json()["summary"]
        assert summary["trade_count"] == 1
        assert summary["loss_count"] == 1
        assert summary["recurring_supported_contributors"].get("POSITION_MANAGEMENT") == 1

    def test_malformed_date_returns_400(self, client):
        resp, _ = _get_daily(client, {"date": "not-a-date", "market": "US"}, [])
        assert resp.status_code == 400

    def test_cross_user_isolation_filters_by_authenticated_user(self, client):
        rows = [_row(trade_id=1, owner="user-aaa", exit_price=120.0)]
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows, auth_sub="user-aaa")
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "user_id = %s" in sql
        assert params[0] == "user-aaa"

    def test_other_users_trade_excluded_even_within_window(self, client):
        rows = [_row(trade_id=1, owner="someone-else", exit_price=120.0)]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        assert resp.status_code == 200
        assert resp.json()["trades"] == []

    def test_market_filter_adds_sql_predicate(self, client):
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "IN"}, [])
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "AND market = %s" in sql
        assert params[-1] == "IN"

    def test_market_all_omits_market_predicate(self, client):
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "ALL"}, [])
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "market = %s" not in sql

    def test_market_local_day_boundary_us_trade_after_local_midnight_excluded(self, client):
        still_june2_et = _row(trade_id=1, market="US", closed_at=dt.datetime(2026, 6, 3, 3, 0, tzinfo=dt.timezone.utc))
        already_june3_et = _row(trade_id=2, market="US", closed_at=dt.datetime(2026, 6, 3, 5, 30, tzinfo=dt.timezone.utc))

        resp_june2, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [still_june2_et, already_june3_et])
        assert {t["trade_id"] for t in resp_june2.json()["trades"]} == {1}

        resp_june3, _ = _get_daily(client, {"date": "2026-06-03", "market": "US"}, [still_june2_et, already_june3_et])
        assert {t["trade_id"] for t in resp_june3.json()["trades"]} == {2}

    def test_missing_auth_header_returns_401(self, client):
        resp = client.get("/api/paper-trading/postmortem/daily", params={"date": "2026-06-02"})
        assert resp.status_code == 401

    def test_daily_summary_uses_recurring_supported_contributors_not_root_cause(self, client):
        rows = [_row(trade_id=1, exit_price=95.0, stop_loss=10.0, target_price=101.0, exit_reason="MANUAL")]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        body = resp.json()
        assert "recurring_supported_contributors" in body["summary"]
        assert "recurring_conflicting_contributors" in body["summary"]
        assert "recurring_not_assessable_count" in body["summary"]


@pytest.mark.regression
class TestFeatureFlagGating:
    """PR #32 pre-merge correction — the daily endpoint is DISABLED by
    default. These tests override the file's own autouse _feature_enabled
    fixture per-case to prove the disabled path independently."""

    def test_disabled_by_default_returns_404(self, client, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_DAILY_ENABLED", raising=False)
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [_row(trade_id=1)])
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "FEATURE_NOT_ENABLED"

    def test_disabled_performs_no_database_query(self, client, monkeypatch):
        """The recorder tracks every conn.execute() call — zero calls
        proves the disabled path never reaches the query, not just that
        it returns an error status."""
        monkeypatch.delenv("TRADE_POSTMORTEM_DAILY_ENABLED", raising=False)
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [_row(trade_id=1)])
        assert resp.status_code == 404
        assert recorder.calls == []

    def test_disabled_response_exposes_no_internal_detail(self, client, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_DAILY_ENABLED", raising=False)
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [])
        body = resp.json()
        assert list(body["detail"].keys()) == ["error_code"]
        assert body["detail"]["error_code"] == "FEATURE_NOT_ENABLED"

    def test_explicit_false_returns_404(self, client, monkeypatch):
        monkeypatch.setenv("TRADE_POSTMORTEM_DAILY_ENABLED", "false")
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [])
        assert resp.status_code == 404

    def test_invalid_value_returns_404(self, client, monkeypatch):
        monkeypatch.setenv("TRADE_POSTMORTEM_DAILY_ENABLED", "maybe")
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [])
        assert resp.status_code == 404

    def test_explicit_true_enables_normal_response(self, client, monkeypatch):
        monkeypatch.setenv("TRADE_POSTMORTEM_DAILY_ENABLED", "true")
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [_row(trade_id=1, exit_price=120.0)])
        assert resp.status_code == 200
        assert resp.json()["summary"]["trade_count"] == 1

    def test_disabled_takes_priority_over_malformed_date(self, client, monkeypatch):
        """The gate must be checked BEFORE date parsing — a malformed date
        must not leak whether the feature would otherwise have validated
        it, and must not exercise any parsing logic while disabled."""
        monkeypatch.delenv("TRADE_POSTMORTEM_DAILY_ENABLED", raising=False)
        resp, _ = _get_daily(client, {"date": "not-a-date", "market": "US"}, [])
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "FEATURE_NOT_ENABLED"


@pytest.mark.regression
class TestExistingPhase1EndpointUnaffected:
    """PR #32 pre-merge correction — the gate must scope ONLY to the new
    daily endpoint; GET /postmortem/{trade_id} (Phase 1) must be completely
    unaffected regardless of the new flag's state."""

    def test_single_trade_endpoint_works_when_daily_flag_disabled(self, client, monkeypatch):
        monkeypatch.delenv("TRADE_POSTMORTEM_DAILY_ENABLED", raising=False)

        # Phase 1's own endpoint pops fetchone() results sequentially:
        # first the trade row, then None for "no entry snapshot exists".
        class _SequentialFetchoneConn:
            def __init__(self, results):
                self._results = list(results)

            def execute(self, sql, params=None):
                return self

            def fetchone(self):
                return self._results.pop(0) if self._results else None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        single_recorder = _SequentialFetchoneConn([_row(trade_id=1, exit_price=120.0)])

        @contextmanager
        def _fake_conn():
            yield single_recorder

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
        ):
            resp = client.get("/api/paper-trading/postmortem/1", headers=_auth("user-aaa"))
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "WIN"
