"""
Wave C, Gate WC-K — real-PostgreSQL behavioral proof for the
authorization-safe, side-effect-free current-report read API:
GET /api/paper-trading/{trade_id}/current-report.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest

from tests.postgres_integration.conftest import ensure_portfolio, make_auth_header

pytestmark = pytest.mark.postgres_integration

_ET = ZoneInfo("America/New_York")

# A valid structured_report satisfying StructuredReportModel/
# PricePathSection/VersionAndProvenance — every current 1.2.0 report
# unconditionally has structured_report.price_path.version_and_provenance.
# Used only where a test expects READY; malformed-report tests keep
# their own intentionally-broken structured_report stubs.
_VALID_STRUCTURED_REPORT = {
    "price_path": {
        "version_and_provenance": {
            "report_schema_version": "1.2.0", "calculation_version": "calc-v1",
            "numerical_rules_version": "1.0.0", "governed_semantic_rules_version": "1.0.0",
            "governed_claim_rules_version": "1.0.0", "entry_snapshot_schema_version": None,
            "exit_snapshot_schema_version": None, "level_history_contract_version": None,
            "source_version": "1.0.0",
        },
    },
}


def _fake_none(*a, **k):
    return []


@pytest.fixture(autouse=True)
def _patch_price_path_provider(monkeypatch):
    from services.postmortem import price_path_generation
    from services.postmortem.price_path_acquisition import acquire_price_path_evidence as _real_acquire

    def _fake_acquire(*, fetch_bars_fn=None, fetch_splits_fn=None, fetch_dividends_fn=None, **kwargs):
        return _real_acquire(fetch_bars_fn=_fake_none, fetch_splits_fn=_fake_none, fetch_dividends_fn=_fake_none, **kwargs)

    monkeypatch.setattr(price_path_generation, "acquire_price_path_evidence", _fake_acquire)


def _buy(client, user_id, **overrides):
    body = {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}
    body.update(overrides)
    return client.post("/api/paper-trading/buy", json=body, headers=make_auth_header(user_id))


def _sell(client, user_id, trade_id, **overrides):
    body = {"price": 108.0, "exit_reason": "MANUAL"}
    body.update(overrides)
    return client.post(f"/api/paper-trading/sell/{trade_id}", json=body, headers=make_auth_header(user_id))


def _current_report(client, user_id, trade_id):
    return client.get(f"/api/paper-trading/{trade_id}/current-report", headers=make_auth_header(user_id))


def _open_and_close(client, pg_conn, user_id):
    ensure_portfolio(pg_conn, user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, user_id).json()["trade_id"]
    resp = _sell(client, user_id, trade_id)
    assert resp.status_code == 200
    return trade_id


# ============================= WC-K-02/03 — auth and ownership ============================= #

def test_unauthenticated_request_is_rejected(client, pg_conn, unique_user_id):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    resp = client.get(f"/api/paper-trading/{trade_id}/current-report")
    assert resp.status_code in (401, 403), f"expected 401/403 for an unauthenticated request, got {resp.status_code}"


def test_nonexistent_trade_returns_404(client, pg_conn, unique_user_id):
    resp = _current_report(client, unique_user_id, 999_999_999)
    assert resp.status_code == 404
    # WC-K-11 — a header set on FastAPI's injected Response does NOT
    # survive a raised HTTPException; this proves the route carries the
    # cache policy explicitly on its 404 path too, not only on 200s.
    assert resp.headers.get("cache-control") == "private, no-store"


def test_another_users_trade_is_indistinguishable_from_nonexistent(client, pg_conn, unique_user_id):
    victim_id = f"{unique_user_id}-victim"
    trade_id = _open_and_close(client, pg_conn, victim_id)

    attacker_id = f"{unique_user_id}-attacker"
    resp_attacker = _current_report(client, attacker_id, trade_id)
    resp_nonexistent = _current_report(client, attacker_id, 999_999_999)

    assert resp_attacker.status_code == 404
    assert resp_attacker.status_code == resp_nonexistent.status_code
    assert resp_attacker.json() == resp_nonexistent.json(), (
        "another user's trade must be byte-for-byte indistinguishable from a nonexistent trade_id"
    )
    assert resp_attacker.headers.get("cache-control") == "private, no-store"
    assert resp_attacker.headers.get("cache-control") == resp_nonexistent.headers.get("cache-control")


# ============================= WC-K-06 — availability-state mapping ============================= #

def test_open_owned_trade_is_not_eligible(client, pg_conn, unique_user_id, monkeypatch):
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, unique_user_id).json()["trade_id"]

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert resp.json()["availability"] == "NOT_ELIGIBLE"
    assert resp.headers.get("cache-control") == "private, no-store"


def test_closed_trade_no_outbox_no_report_is_not_available(client, pg_conn, unique_user_id, monkeypatch):
    """Close happens with the capability at its default (off) so /sell
    never auto-creates a current outbox row; the capability is enabled
    only for the read itself, so a fresh closed trade with no explicit
    generation request shows NOT_AVAILABLE, never a fabricated report."""
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "NOT_AVAILABLE"
    assert body["structured_report"] is None
    assert resp.headers.get("cache-control") == "private, no-store"


def test_ready_report_returns_persisted_complete_or_limited_evidence(client, pg_conn, unique_user_id, monkeypatch):
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    trade_id = _open_and_close(client, pg_conn, unique_user_id)

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["report_schema_version"] == "1.2.0"
    assert body["status"] in ("COMPLETE", "LIMITED_EVIDENCE")
    assert body["structured_report"] is not None
    assert "price_path" in body["structured_report"]
    # WC-K-13 — generated_at must be the real persisted immutable
    # report timestamp, never fabricated from request time.
    assert body["generated_at"] is not None
    row = pg_conn.execute(
        """SELECT generated_at FROM paper_trade_postmortem_report
           WHERE paper_trade_id = %s AND report_schema_version = %s
             AND calculation_version = %s AND attribution_rules_version = %s""",
        (trade_id, body["report_schema_version"], body["calculation_version"], body["attribution_rules_version"]),
    ).fetchone()
    persisted_generated_at = row[0]
    assert datetime.fromisoformat(body["generated_at"]) == persisted_generated_at.astimezone(timezone.utc), (
        "response generated_at must equal the persisted report row's own generated_at column, not current time"
    )
    # WC-K-11 — authenticated response must never be safe for a shared cache.
    assert resp.headers.get("cache-control") == "private, no-store"


# ============================= WC-K-14 — persisted version provenance ============================= #

def test_evidence_bundle_version_reflects_the_persisted_row_not_a_code_constant(client, pg_conn, unique_user_id, monkeypatch):
    """Adversarial proof: current_target_identity() (built from CURRENT
    code constants) is used only to LOCATE the current report row via
    its (schema_version, calculation_version, attribution_rules_version)
    key — the response's evidence_bundle_version, which is NOT part of
    that lookup key, must come from that row's own persisted column,
    never be synthesized from a currently-imported constant.

    paper_trade_postmortem_report rows are immutable (an UPDATE is
    rejected by a database trigger — discovered by this test's first
    attempt at direct UPDATE, which failed with
    reject_paper_trade_postmortem_report_update()), so this seeds the
    current-version row directly via report_store.persist_report with a
    marker evidence_bundle_version no code constant could ever equal,
    rather than generating one first and mutating it afterward."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    # Trade A: capability ON at close time, so /sell auto-generates a
    # REAL report through the actual production pipeline — the source
    # of a realistic structured_report/claims/evidence_items/
    # source_manifest body (never a hand-written stub), so this test
    # stays valid against a future strict typed response contract.
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    real_trade_id = _open_and_close(client, pg_conn, unique_user_id)

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    real_row = pg_conn.execute(
        """SELECT structured_report, evidence_items, claims, source_manifest, evidence_gaps, warnings, status
           FROM paper_trade_postmortem_report
           WHERE paper_trade_id = %s AND report_schema_version = %s
             AND calculation_version = %s AND attribution_rules_version = %s""",
        (real_trade_id, schema_v, calc_v, rules_v),
    ).fetchone()
    (real_structured, real_evidence_items, real_claims, real_source_manifest,
     real_evidence_gaps, real_warnings, real_status) = real_row

    # Trade B: capability OFF at close time, so /sell never
    # auto-generates a report at this identity for THIS trade — a
    # distinct trade_id also avoids the ON CONFLICT DO NOTHING
    # collision an UPDATE-in-place or a second INSERT for real_trade_id
    # would hit against its own immutable row.
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "0")
    other_trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    marker = "9.9.9-persisted-provenance-adversarial-marker"
    report_store.persist_report(
        pg_conn, paper_trade_id=other_trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version=marker, status=real_status,
        structured_report=real_structured, evidence_items=real_evidence_items, claims=real_claims,
        source_manifest=real_source_manifest, evidence_gaps=real_evidence_gaps, warnings=real_warnings,
    )

    resp = _current_report(client, unique_user_id, other_trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["evidence_bundle_version"] == marker, (
        "evidence_bundle_version must be read from the persisted report row, "
        "never fabricated from a current code constant"
    )
    # Round-trip proof against the REAL captured body — note price-path
    # enhancement is not guaranteed on every real generation (eligibility
    # can vary), so this asserts exact equality with whatever real_row
    # actually captured, rather than assuming a "price_path" key exists.
    assert body["structured_report"] == real_structured
    assert body["claims"] == real_claims
    assert body["evidence_items"] == real_evidence_items


def test_terminal_outbox_missing_report_is_integrity_contradiction(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status, completed_at)
           VALUES (%s, %s, %s, %s, %s, 'COMPLETE', now())""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert resp.json()["availability"] == "INTEGRITY_CONTRADICTION"
    assert resp.headers.get("cache-control") == "private, no-store"


def test_terminal_failure_outbox_is_reported(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status, completed_at, last_error_code)
           VALUES (%s, %s, %s, %s, %s, 'FAILED_TERMINAL', now(), 'MAX_ATTEMPTS_EXCEEDED')""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "TERMINAL_FAILURE"
    # WC-K-09 — no raw error code/exception leaked to the client.
    assert "MAX_ATTEMPTS_EXCEEDED" not in str(body)
    assert resp.headers.get("cache-control") == "private, no-store"


def test_pending_outbox_is_processing(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    pg_conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, status)
           VALUES (%s, %s, %s, %s, %s, 'PENDING')""",
        (trade_id, unique_user_id, schema_v, calc_v, rules_v),
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert resp.json()["availability"] == "PROCESSING"
    assert resp.headers.get("cache-control") == "private, no-store"


# ============================= WC-K-05 — side-effect-free proof ============================= #

def test_get_never_calls_provider_acquisition(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    # Enabled AFTER close, so /sell never auto-generates — this proves
    # GET calls no provider even when the trade IS eligible and the
    # capability IS on, not merely because the feature is off.
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    calls = {"n": 0}
    from services.postmortem import price_path_generation

    def _counting_acquire(**kwargs):
        calls["n"] += 1
        raise AssertionError("GET must never call acquire_price_path_evidence")

    monkeypatch.setattr(price_path_generation, "acquire_price_path_evidence", _counting_acquire)

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert calls["n"] == 0, "GET /current-report must never trigger provider acquisition."


def test_get_inserts_no_outbox_row_and_no_report_row(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    before_outbox = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    before_report = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s AND report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()[0]

    for _ in range(3):
        resp = _current_report(client, unique_user_id, trade_id)
        assert resp.status_code == 200

    after_outbox = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    after_report = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s AND report_schema_version = '1.2.0'",
        (trade_id,),
    ).fetchone()[0]

    assert after_outbox == before_outbox, "GET must never insert an outbox row."
    assert after_report == before_report, "GET must never insert a report row."


def test_get_does_not_mutate_trade_row(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    before = pg_conn.execute(
        "SELECT status, exit_price, closed_at FROM paper_trades WHERE id = %s", (trade_id,),
    ).fetchone()

    for _ in range(3):
        _current_report(client, unique_user_id, trade_id)

    after = pg_conn.execute(
        "SELECT status, exit_price, closed_at FROM paper_trades WHERE id = %s", (trade_id,),
    ).fetchone()
    assert before == after, "GET must never mutate the trade row."


# ============================= WC-K-10 — historical-version isolation ============================= #

def test_historical_1_1_0_report_never_returned_as_current(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version="1.1.0", calculation_version="1.1.0-legacy-test", attribution_rules_version="1.1.0-legacy-test",
        evidence_bundle_version="1.0.0", status="COMPLETE", structured_report={"legacy": True}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    # No 1.2.0 report was ever generated for this trade — the legacy 1.1.0
    # row must never be silently presented as the current report.
    assert body["report_schema_version"] != "1.1.0"
    if body["structured_report"] is not None:
        assert body["structured_report"].get("legacy") is not True


# ============================= WC-K — malformed persisted report, fail-closed ============================= #

def test_malformed_status_at_current_identity_fails_closed_to_integrity_contradiction(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """The database's status column carries no CHECK constraint (only a
    convention comment: 'COMPLETE | LIMITED_EVIDENCE | FAILED_TERMINAL')
    — report_store.persist_report also performs no validation, so a
    genuinely malformed status can reach the database. The typed
    CurrentReportReadResponse's own Literal["COMPLETE","LIMITED_EVIDENCE"]
    constraint must reject it, and the route's fail-closed conversion
    boundary must convert that rejection into a sanitized
    INTEGRITY_CONTRADICTION rather than a raw 500."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close — no auto-generation
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="malformed-status-test",
        status="THIS_IS_NOT_A_VALID_STATUS",  # <-- the malformation under test
        structured_report={"marker": "malformed-status-test"}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "INTEGRITY_CONTRADICTION"
    # No READY payload, no raw persisted content, no validation trace.
    assert body["structured_report"] is None
    assert body["status"] is None
    assert body["claims"] is None
    assert "THIS_IS_NOT_A_VALID_STATUS" not in str(body)
    assert "marker" not in str(body)
    assert "ValidationError" not in str(body)
    assert resp.headers.get("cache-control") == "private, no-store"


def test_malformed_report_causes_no_mutation_or_recovery(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close — no auto-generation
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="malformed-no-mutation-test", status="ALSO_NOT_VALID",
        structured_report={}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )

    before_report_count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    before_outbox_count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]

    for _ in range(3):
        resp = _current_report(client, unique_user_id, trade_id)
        assert resp.status_code == 200
        assert resp.json()["availability"] == "INTEGRITY_CONTRADICTION"

    after_report_count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    after_outbox_count = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]

    assert after_report_count == before_report_count
    assert after_outbox_count == before_outbox_count


# ============================= WC-K-15 — capability-disabled behavior ============================= #
# Note: the `client`/pg_conn fixtures run with the capability at its
# real production default (unset/off) unless a test explicitly enables
# it via monkeypatch.setenv — so every test below needs NO monkeypatch
# at all to exercise the disabled path, which is itself evidence that
# "disabled" is the safe, unauthorized-by-default state.

def test_disabled_owned_open_trade_returns_feature_disabled(client, pg_conn, unique_user_id):
    ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
    trade_id = _buy(client, unique_user_id).json()["trade_id"]

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "FEATURE_DISABLED"
    assert body["structured_report"] is None


def test_disabled_owned_closed_trade_returns_feature_disabled(client, pg_conn, unique_user_id):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "FEATURE_DISABLED"
    assert body["structured_report"] is None


def test_disabled_owned_trade_with_persisted_report_exposes_no_report_contents(client, pg_conn, unique_user_id, monkeypatch):
    """Generate a real 1.2.0 report with the capability ON, then turn it
    OFF and re-read — disabled must hide the already-persisted report,
    never leak it just because the row already exists in the database."""
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    ready_resp = _current_report(client, unique_user_id, trade_id)
    assert ready_resp.json()["availability"] == "READY"

    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "0")
    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "FEATURE_DISABLED"
    assert body["structured_report"] is None
    assert body["claims"] is None
    assert body["report_schema_version"] is None


def test_disabled_nonexistent_trade_still_returns_404(client, pg_conn, unique_user_id):
    resp = _current_report(client, unique_user_id, 999_999_999)
    assert resp.status_code == 404
    assert resp.headers.get("cache-control") == "private, no-store"


def test_disabled_other_users_trade_is_still_indistinguishable_from_nonexistent(client, pg_conn, unique_user_id):
    """Ownership is enforced BEFORE the capability check — a disabled
    capability must never become a side channel for probing whether
    another user's trade_id exists."""
    victim_id = f"{unique_user_id}-victim"
    trade_id = _open_and_close(client, pg_conn, victim_id)

    attacker_id = f"{unique_user_id}-attacker"
    resp_attacker = _current_report(client, attacker_id, trade_id)
    resp_nonexistent = _current_report(client, attacker_id, 999_999_999)

    assert resp_attacker.status_code == 404
    assert resp_attacker.status_code == resp_nonexistent.status_code
    assert resp_attacker.json() == resp_nonexistent.json()


def test_disabled_response_performs_no_writes_or_processing(client, pg_conn, unique_user_id):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)

    calls = {"n": 0}
    from services.postmortem import price_path_generation

    def _counting_acquire(**kwargs):
        calls["n"] += 1
        raise AssertionError("disabled-capability GET must never call acquire_price_path_evidence")

    import unittest.mock
    with unittest.mock.patch.object(price_path_generation, "acquire_price_path_evidence", _counting_acquire):
        before_outbox = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
        ).fetchone()[0]
        resp = _current_report(client, unique_user_id, trade_id)
        after_outbox = pg_conn.execute(
            "SELECT count(*) FROM paper_trade_postmortem_outbox WHERE paper_trade_id = %s", (trade_id,),
        ).fetchone()[0]

    assert resp.status_code == 200
    assert resp.json()["availability"] == "FEATURE_DISABLED"
    assert calls["n"] == 0
    assert after_outbox == before_outbox


def test_disabled_response_has_safe_cache_policy(client, pg_conn, unique_user_id):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "private, no-store"


# ============================= WC-K-14 — report-level source_manifest ============================= #

def test_report_level_source_manifest_round_trips_adversarial_marker_values(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Real-PostgreSQL adversarial proof for ReportSourceManifest: every
    field, plus an unknown future JSON-safe key, is set to a marker
    value no code constant could ever equal, and the API response must
    reflect them exactly — never substituted from a current constant."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close — no auto-generation
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    marker_manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": "marker-exit-snapshot-schema",
        # Governed vocabulary field — must be a real Literal value, not
        # a free-form marker, so this uses SERVER_VERIFIED itself as
        # the "adversarial" value (it's simply not the value a plain
        # manual-close report would carry, proving the response isn't
        # silently defaulting to something else).
        "exit_trigger_timing_verification": "SERVER_VERIFIED",
        "exit_evidence_rules_version": "marker-exit-evidence-rules",
        "phase1_calculation_version": "marker-phase1-calc",
        "attribution_rules_version": "marker-attribution-rules",
        "price_path_calculation_version": "marker-price-path-calc",
        "price_path_rules_version": "marker-price-path-rules", "governed_rules_version": "marker-governed-rules",
        "some_future_field_not_yet_modelled": "marker-future-value",
    }
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="source-manifest-marker-test", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=marker_manifest, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["source_manifest"] == marker_manifest, (
        "report-level source_manifest must round-trip exactly from the persisted row, "
        "including an unknown future key, never substituted from a current constant"
    )


def test_report_level_source_manifest_preserves_historical_absence_of_price_path_key(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Companion historical test: a persisted report whose source_manifest
    genuinely omits price_path_calculation_version (a plain Sprint 2
    report, never price-path-enhanced) must keep that key ABSENT in the
    API response — never synthesized as an explicit null."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close — no auto-generation
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    manifest_without_price_path_key = {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": "1.0.0", "exit_trigger_timing_verification": "SERVER_VERIFIED",
        "exit_evidence_rules_version": "1.0.0",
        "phase1_calculation_version": "1.0.0", "attribution_rules_version": "1.0.0",
            "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
        # price_path_calculation_version intentionally omitted entirely.
    }
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="historical-absence-test", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=manifest_without_price_path_key, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert "price_path_calculation_version" not in body["source_manifest"], (
        "a genuinely absent historical-optional key must stay absent in the response JSON, "
        "never synthesized as an explicit null"
    )


# ============================= source_manifest semantic consistency, fail-closed ============================= #

def _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, *, marker):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store
    from datetime import datetime as _dt, timezone as _tz

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=_dt.now(_tz.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version=marker, status="COMPLETE",
        structured_report={"marker": marker}, evidence_items=[], claims=[],
        source_manifest=manifest, evidence_gaps=[], warnings=[],
    )


def _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, forbidden_strings):
    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "INTEGRITY_CONTRADICTION"
    assert body["structured_report"] is None
    assert body["source_manifest"] is None
    for forbidden in forbidden_strings:
        assert forbidden not in str(body)
    assert resp.headers.get("cache-control") == "private, no-store"


def test_no_exit_snapshot_with_non_null_schema_version_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": False,
        "exit_snapshot_schema_version": "should-not-be-set",  # <-- malformation
        "exit_trigger_timing_verification": None,
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="no-exit-nonnull-schema")
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["should-not-be-set"])


def test_no_exit_snapshot_with_non_null_trigger_timing_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": False,
        "exit_snapshot_schema_version": None,
        "exit_trigger_timing_verification": "SERVER_VERIFIED",  # <-- malformation
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="no-exit-nonnull-trigger")
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, [])


def test_has_exit_snapshot_missing_schema_version_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": None,  # <-- malformation
        "exit_trigger_timing_verification": "SERVER_VERIFIED",
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="has-exit-missing-schema")
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, [])


def test_invalid_trigger_timing_vocabulary_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": "1.0.0",
        "exit_trigger_timing_verification": "NOT_A_GOVERNED_VALUE",  # <-- malformation
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="invalid-trigger-vocab")
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["NOT_A_GOVERNED_VALUE"])


def test_explicit_null_price_path_calculation_version_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": False,
        "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
        "price_path_calculation_version": None,  # <-- malformation: explicit null, not absence
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="explicit-null-price-path")
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, [])


def test_malformed_source_manifest_causes_no_mutation(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    manifest = {
        "has_entry_snapshot": True, "has_exit_snapshot": True,
        "exit_snapshot_schema_version": None,
        "exit_trigger_timing_verification": None,
        "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
        "attribution_rules_version": "1.0.0",
        "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
    }
    _seed_current_manifest(pg_conn, trade_id, unique_user_id, manifest, marker="no-mutation-check")

    before = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    for _ in range(3):
        resp = _current_report(client, unique_user_id, trade_id)
        assert resp.json()["availability"] == "INTEGRITY_CONTRADICTION"
    after = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    assert after == before


# ============================= claims/evidence_items provenance ============================= #

def test_claims_and_evidence_items_round_trip_adversarial_marker_values(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Real-PostgreSQL adversarial proof for PostmortemClaimModel and
    EvidenceItemModel: a claim and an evidence item with marker values
    for every typed field round-trip exactly through the canonical
    endpoint — never substituted or dropped."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close — no auto-generation
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    marker_evidence_item = {
        "evidence_id": "EV-marker-id", "category": "marker-category", "name": "marker-name",
        "value": {"nested": "marker-value", "n": 42}, "units": "marker-units",
        "observation_timestamp": "2026-06-01T12:00:00+00:00",
        # source/category/name/units remain genuinely free-form marker
        # strings; source_type/verification_level/freshness_status are
        # now governed enums (services.postmortem.evidence) and must be
        # real values — using ones a plain manual-close report would
        # NOT ordinarily carry still proves the response isn't silently
        # defaulting to something else.
        "source": "marker-source", "source_type": "EXTERNAL_UNOFFICIAL_DAILY",
        "verification_level": "DIRECTLY_OBSERVED", "freshness_status": "STALE",
        "limitations": ["marker-limitation-1"],
    }
    marker_claim = {
        "claim_id": "CLM-marker-id", "report_section": "marker-section", "factor": "marker-factor",
        "claim_text": "marker claim text", "evidence_class": "DIRECTLY_OBSERVED",
        "confidence_band": "HIGH", "supporting_evidence_ids": ["EV-marker-id"],
        "opposing_evidence_ids": [], "missing_evidence": [], "contradiction_flags": [],
        "rule_id": "marker-rule-id", "rule_version": "marker-rule-version",
        "limitations": ["marker-claim-limitation"],
    }
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="claim-evidence-marker-test", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT,
        evidence_items=[marker_evidence_item], claims=[marker_claim],
        source_manifest={
            "has_entry_snapshot": True, "has_exit_snapshot": False,
            "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
            "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
            "attribution_rules_version": "1.0.0",
            "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
        },
        evidence_gaps=["marker-gap"], warnings=["marker-warning"],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["claims"] == [marker_claim]
    # evidence_items[0] is now typed (EvidenceItemModel.observation_timestamp:
    # datetime), so pydantic normalizes the persisted ISO string to its own
    # canonical form ("...Z" instead of "...+00:00") — compare everything
    # except that one field literally, and that field by parsed value.
    got_evidence = body["evidence_items"][0]
    expected_evidence = marker_evidence_item
    assert {k: v for k, v in got_evidence.items() if k != "observation_timestamp"} == {
        k: v for k, v in expected_evidence.items() if k != "observation_timestamp"
    }
    assert datetime.fromisoformat(got_evidence["observation_timestamp"]) == datetime.fromisoformat(
        expected_evidence["observation_timestamp"]
    )
    assert body["evidence_gaps"] == ["marker-gap"]
    assert body["warnings"] == ["marker-warning"]


def test_malformed_claim_missing_rule_id_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    """Re-added after the root-cause canonicalization fix (governed
    dataclass entries are now converted to dicts before persistence,
    proven by test_real_price_path_report_persists_claims_and_evidence_
    as_json_objects) and re-enabling list[PostmortemClaimModel] typing."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    malformed_claim = {
        "claim_id": "CLM-malformed", "report_section": "x", "factor": "x",
        "claim_text": "x", "evidence_class": "DIRECTLY_OBSERVED", "confidence_band": "HIGH",
        "supporting_evidence_ids": [], "opposing_evidence_ids": [], "missing_evidence": [],
        "contradiction_flags": [],
        # rule_id and rule_version intentionally omitted — malformed.
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="malformed-claim-test", status="COMPLETE",
        structured_report={"marker": "malformed-claim-test"}, evidence_items=[], claims=[malformed_claim],
        source_manifest={
            "has_entry_snapshot": True, "has_exit_snapshot": False,
            "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
            "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
            "attribution_rules_version": "1.0.0",
            "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
        },
        evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["CLM-malformed"])


# ============================= persisted claims/evidence_items shape ============================= #

def test_real_price_path_report_persists_claims_and_evidence_as_json_objects(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Real defect regression: a governed price-path EvidenceItem/
    PostmortemClaim dataclass instance previously reached
    report_store.persist_report unconverted, and json.dumps(...,
    default=str) silently stringified it into an opaque dataclass repr
    instead of persisting a governed JSON object — current_report_
    generation.build_current_report_payload now canonicalizes via
    dataclasses.asdict before the merge, and report_store's own
    encoder additionally refuses (rather than stringifies) any
    non-dataclass/Enum/datetime object that still reaches it.

    This test proves the PERSISTED SHAPE only (jsonb_typeof, no value
    output) — real content-value proof already exists in
    test_ready_report_returns_persisted_complete_or_limited_evidence
    and the adversarial marker round-trip tests above."""
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    trade_id = _open_and_close(client, pg_conn, unique_user_id)

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    assert resp.json()["availability"] == "READY"

    claim_shape_rows = pg_conn.execute(
        """SELECT idx, jsonb_typeof(elem)
           FROM paper_trade_postmortem_report,
                jsonb_array_elements(claims) WITH ORDINALITY AS t(elem, idx)
           WHERE paper_trade_id = %s""",
        (trade_id,),
    ).fetchall()
    evidence_shape_rows = pg_conn.execute(
        """SELECT idx, jsonb_typeof(elem)
           FROM paper_trade_postmortem_report,
                jsonb_array_elements(evidence_items) WITH ORDINALITY AS t(elem, idx)
           WHERE paper_trade_id = %s""",
        (trade_id,),
    ).fetchall()

    assert claim_shape_rows, "expected at least one persisted claim for a real generated report"
    for idx, typeof in claim_shape_rows:
        assert typeof == "object", f"claims[{idx}] has jsonb_typeof={typeof!r}, expected 'object' (not 'string')"

    for idx, typeof in evidence_shape_rows:
        assert typeof == "object", f"evidence_items[{idx}] has jsonb_typeof={typeof!r}, expected 'object'"

    # Belt-and-suspenders: no dataclass repr prefix survives, using a
    # count-only ILIKE check that never selects the actual content.
    repr_leak_count = pg_conn.execute(
        """SELECT count(*) FROM paper_trade_postmortem_report
           WHERE paper_trade_id = %s
             AND (claims::text ILIKE '%%PostmortemClaim(%%' OR evidence_items::text ILIKE '%%EvidenceItem(%%')""",
        (trade_id,),
    ).fetchone()[0]
    assert repr_leak_count == 0, "found a dataclass repr string leaked into persisted claims/evidence_items"


def test_governed_no_bars_fallback_report_persists_claims_as_json_objects(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Same shape proof, specifically for the governed no-bars/fallback
    branch (build_governed_price_path_payload's bundle=None path) —
    the exact branch that constructs GovernedLevelTouchConclusion-based
    EvidenceItem/PostmortemClaim instances via build_governed_touch_claim
    and build_governed_order_claim."""
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # _fake_none fetchers -> bundle=None -> fallback path

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["status"] == "LIMITED_EVIDENCE"  # no-bars fallback ceilings the report to LIMITED_EVIDENCE

    claim_shape_rows = pg_conn.execute(
        """SELECT jsonb_typeof(elem)
           FROM paper_trade_postmortem_report, jsonb_array_elements(claims) AS elem
           WHERE paper_trade_id = %s""",
        (trade_id,),
    ).fetchall()
    assert claim_shape_rows
    assert all(typeof == "object" for (typeof,) in claim_shape_rows)


# ============================= claim/evidence governance, referential integrity ============================= #

_VALID_MANIFEST = {
    "has_entry_snapshot": True, "has_exit_snapshot": False,
    "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
    "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
    "attribution_rules_version": "1.0.0",
    "price_path_rules_version": "1.0.0", "governed_rules_version": "1.0.0",
}


def test_invalid_governed_enum_in_persisted_claim_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    claim = {
        "claim_id": "CLM-x", "report_section": "s", "factor": "f", "claim_text": "t",
        "evidence_class": "NOT_A_GOVERNED_VALUE", "confidence_band": "HIGH",
        "supporting_evidence_ids": [], "opposing_evidence_ids": [], "missing_evidence": [],
        "contradiction_flags": [], "rule_id": "r1", "rule_version": "1.0.0",
    }
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="invalid-claim-enum", status="COMPLETE",
        structured_report={"marker": "invalid-claim-enum"}, evidence_items=[], claims=[claim],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["NOT_A_GOVERNED_VALUE"])


def test_invalid_governed_enum_in_persisted_evidence_item_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    evidence_item = {
        "evidence_id": "EV-x", "category": "c", "name": "n", "value": 1, "units": None,
        "observation_timestamp": None, "source": "s", "source_type": "NOT_A_GOVERNED_VALUE",
        "verification_level": "MECHANICALLY_VERIFIED", "freshness_status": "POINT_IN_TIME_VALID",
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="invalid-evidence-enum", status="COMPLETE",
        structured_report={"marker": "invalid-evidence-enum"}, evidence_items=[evidence_item], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["NOT_A_GOVERNED_VALUE"])


def test_dangling_supporting_evidence_reference_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    claim = {
        "claim_id": "CLM-dangling", "report_section": "s", "factor": "f", "claim_text": "t",
        "evidence_class": "DIRECTLY_OBSERVED", "confidence_band": "HIGH",
        "supporting_evidence_ids": ["EV-NONEXISTENT"], "opposing_evidence_ids": [], "missing_evidence": [],
        "contradiction_flags": [], "rule_id": "r1", "rule_version": "1.0.0",
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="dangling-support", status="COMPLETE",
        structured_report={"marker": "dangling-support"}, evidence_items=[], claims=[claim],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["EV-NONEXISTENT", "CLM-dangling"])


def test_unexpected_extra_claim_field_fails_closed_under_strict_policy(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    claim = {
        "claim_id": "CLM-extra", "report_section": "s", "factor": "f", "claim_text": "t",
        "evidence_class": "DIRECTLY_OBSERVED", "confidence_band": "HIGH",
        "supporting_evidence_ids": ["EV-1"], "opposing_evidence_ids": [], "missing_evidence": [],
        "contradiction_flags": [], "rule_id": "r1", "rule_version": "1.0.0",
        "unexpected_field": "surprise",
    }
    evidence_item = {
        "evidence_id": "EV-1", "category": "c", "name": "n", "value": 1, "units": None,
        "observation_timestamp": None, "source": "s", "source_type": "SERVER_DERIVED",
        "verification_level": "MECHANICALLY_VERIFIED", "freshness_status": "POINT_IN_TIME_VALID",
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="extra-field", status="COMPLETE",
        structured_report={"marker": "extra-field"}, evidence_items=[evidence_item], claims=[claim],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["surprise", "unexpected_field"])


# ============================= structured_report.price_path.version_and_provenance (A4) ============================= #

def test_structured_report_provenance_round_trips_adversarial_marker_values(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Real-PostgreSQL adversarial proof for VersionAndProvenance: every
    nested provenance field, plus an unrelated top-level structured_
    report section, round trips exactly through the canonical endpoint."""
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    marker_structured_report = {
        "postmortem": {"outcome": "marker-outcome"},
        "price_path": {
            "raw_evidence": {"marker": "raw-evidence-marker"},
            "version_and_provenance": {
                "report_schema_version": "marker-schema-v", "calculation_version": "marker-calc-v",
                "numerical_rules_version": "marker-numerical-v", "governed_semantic_rules_version": "marker-semantic-v",
                "governed_claim_rules_version": "marker-claim-v", "entry_snapshot_schema_version": "marker-entry-v",
                "exit_snapshot_schema_version": "marker-exit-v", "level_history_contract_version": "marker-history-v",
                "source_version": "marker-source-v",
            },
        },
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="structured-report-marker-test", status="COMPLETE",
        structured_report=marker_structured_report, evidence_items=[], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["structured_report"] == marker_structured_report, (
        "structured_report must round-trip exactly from the persisted row, "
        "including its unrelated top-level 'postmortem' section"
    )


def test_structured_report_missing_price_path_subtree_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="missing-price-path", status="COMPLETE",
        structured_report={"postmortem": {"outcome": "WIN"}},  # no "price_path" key at all
        evidence_items=[], claims=[], source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, [])


def test_structured_report_missing_required_provenance_field_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    incomplete_vap = {
        "report_schema_version": "1.2.0",
        # calculation_version intentionally omitted — malformed.
        "numerical_rules_version": "1.0.0", "governed_semantic_rules_version": "1.0.0",
        "governed_claim_rules_version": "1.0.0", "entry_snapshot_schema_version": None,
        "exit_snapshot_schema_version": None, "level_history_contract_version": None,
        "source_version": "1.0.0",
    }
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="missing-vap-field", status="COMPLETE",
        structured_report={"price_path": {"version_and_provenance": incomplete_vap}},
        evidence_items=[], claims=[], source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, [])


# ============================= supersession (A5) ============================= #

def test_current_report_with_no_predecessor_returns_null_supersedes_id(client, pg_conn, unique_user_id, monkeypatch):
    """Close-time best-effort generation (generation_service.generate_and_
    persist, invoked at sell regardless of the price-path flag — see
    paper_trading.py's sell handler) always persists a real 1.0.0 base
    report for a happy-path close, so a trade that reaches this API via
    _open_and_close always has a genuine predecessor once the 1.2.0 report
    is live-generated. "No predecessor" is therefore not reachable through
    the ordinary close+GET flow — it is exercised here the same way the
    "real predecessor" and "no mutation" tests below control the exact
    persisted identity: by seeding the 1.2.0 row directly at its exact
    target identity (report_store.get_current_report then returns it
    unchanged, without live generation running its own predecessor
    lookup), with supersedes_report_id left at its true default of None."""
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="1.2.0-no-predecessor", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["supersedes_report_id"] is None


def test_current_report_with_a_real_predecessor_returns_the_exact_persisted_predecessor_id(
    client, pg_conn, unique_user_id, monkeypatch,
):
    """Seeds a historical 1.0.0 predecessor row, then a current 1.2.0
    row whose supersedes_report_id points at the predecessor's real
    persisted id — proves the API returns that exact stored id, never
    recalculating or substituting a different value."""
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

    predecessor, _created = report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version="1.0.0", calculation_version="1.0.0-predecessor", attribution_rules_version="1.0.0-predecessor",
        evidence_bundle_version="1.0.0", status="COMPLETE",
        structured_report={"postmortem": {"outcome": "WIN"}}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="1.2.0-successor", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
        supersedes_report_id=predecessor.id,
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["supersedes_report_id"] == predecessor.id


def test_repeated_get_with_supersession_causes_no_mutation(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    predecessor, _ = report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version="1.0.0", calculation_version="1.0.0-p2", attribution_rules_version="1.0.0-p2",
        evidence_bundle_version="1.0.0", status="COMPLETE",
        structured_report={"postmortem": {"outcome": "WIN"}}, evidence_items=[], claims=[],
        source_manifest={}, evidence_gaps=[], warnings=[],
    )
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="1.2.0-successor-2", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
        supersedes_report_id=predecessor.id,
    )

    before = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    for _ in range(3):
        resp = _current_report(client, unique_user_id, trade_id)
        assert resp.json()["supersedes_report_id"] == predecessor.id
    after = pg_conn.execute(
        "SELECT count(*) FROM paper_trade_postmortem_report WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()[0]
    assert after == before


# ================== Wave C, Gate 2 — availability/determinism/no-side-effect matrix ================== #
#
# The state -> availability mapping already has one representative test
# per branch above (test_open_owned_trade_is_not_eligible,
# test_closed_trade_no_outbox_no_report_is_not_available,
# test_ready_report_returns_persisted_complete_or_limited_evidence,
# test_terminal_outbox_missing_report_is_integrity_contradiction,
# test_terminal_failure_outbox_is_reported, test_pending_outbox_is_processing,
# test_disabled_owned_open_trade_returns_feature_disabled,
# test_disabled_owned_closed_trade_returns_feature_disabled). This section
# closes the two branches not yet independently exercised — GENERATING
# with an active lease and GENERATING with an expired lease both mapping
# to PROCESSING identically (the route reads only the outbox `status`
# column, never `lease_expires_at`/`claimed_at`, per direct source read
# of get_current_governed_report) — and FAILED_RETRYABLE -> PROCESSING —
# then adds one reusable exact before/after DB-state snapshot (every
# outbox and report column, not just row counts) applied across every
# governed state, a deterministic-serialization proof, and a proof GET
# never invokes any generation/acquisition/claim/recovery operation in
# any of these states (not only the READY-adjacent one already covered
# by test_get_never_calls_provider_acquisition).

_OUTBOX_COLUMNS = (
    "id, paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version, "
    "requested_rules_version, status, attempt_count, next_attempt_at, last_attempt_at, claimed_at, "
    "lease_expires_at, claimed_by, last_error_code, last_error_summary, source_request_id, created_at, "
    "completed_at"
)
_REPORT_COLUMNS_SNAPSHOT = (
    "id, paper_trade_id, user_id, market, report_trading_date, market_timezone, report_schema_version, "
    "calculation_version, attribution_rules_version, evidence_bundle_version, evidence_hash, status, "
    "structured_report, evidence_items, claims, source_manifest, evidence_gaps, warnings, generated_at, "
    "supersedes_report_id, created_at"
)


def _snapshot_state(pg_conn, trade_id):
    """Exact before/after DB-state proof: every column of every outbox
    and report row for this trade, plus the trade row's own mutable
    fields — not just a row count, so a corrupting UPDATE-in-place would
    be caught even if it left row counts unchanged."""
    outbox_rows = pg_conn.execute(
        f"SELECT {_OUTBOX_COLUMNS} FROM paper_trade_postmortem_outbox "
        "WHERE paper_trade_id = %s ORDER BY id", (trade_id,),
    ).fetchall()
    report_rows = pg_conn.execute(
        f"SELECT {_REPORT_COLUMNS_SNAPSHOT} FROM paper_trade_postmortem_report "
        "WHERE paper_trade_id = %s ORDER BY id", (trade_id,),
    ).fetchall()
    trade_row = pg_conn.execute(
        "SELECT status, exit_price, closed_at FROM paper_trades WHERE id = %s", (trade_id,),
    ).fetchone()
    return (outbox_rows, report_rows, trade_row)


def _insert_outbox(pg_conn, *, trade_id, user_id, schema_v, calc_v, rules_v, status, **extra):
    columns = ["paper_trade_id", "user_id", "requested_report_schema_version",
               "requested_calculation_version", "requested_rules_version", "status"]
    values = [trade_id, user_id, schema_v, calc_v, rules_v, status]
    for col, val in extra.items():
        columns.append(col)
        values.append(val)
    placeholders = ", ".join(["%s"] * len(values))
    pg_conn.execute(
        f"INSERT INTO paper_trade_postmortem_outbox ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values),
    )


def _forbid_all_generation_calls(monkeypatch):
    """Patches every generation/acquisition/claim/recovery entry point
    this GET route must never invoke, each raising immediately if
    called — a single shared guard reused across every Gate 2 state
    test so the forbidden-call list can't silently drift between tests."""
    from services.postmortem import current_report_generation, generation_service, price_path_generation

    def _forbidden(name):
        def _raise(*a, **k):
            raise AssertionError(f"GET /current-report must never call {name}")
        return _raise

    monkeypatch.setattr(price_path_generation, "acquire_price_path_evidence", _forbidden("acquire_price_path_evidence"))
    monkeypatch.setattr(current_report_generation, "process_current_report", _forbidden("process_current_report"))
    monkeypatch.setattr(current_report_generation, "persist_current_report", _forbidden("persist_current_report"))
    monkeypatch.setattr(current_report_generation, "insert_current_outbox_record", _forbidden("insert_current_outbox_record"))
    monkeypatch.setattr(generation_service, "generate_and_persist", _forbidden("generate_and_persist"))


@pytest.mark.parametrize(
    "status,extra",
    [
        ("PENDING", {}),
        ("GENERATING", {"claimed_at": "now()", "lease_expires_at": "now() + interval '5 minutes'", "claimed_by": "'worker-active'"}),
        ("GENERATING", {"claimed_at": "now() - interval '1 hour'", "lease_expires_at": "now() - interval '55 minutes'", "claimed_by": "'worker-expired'"}),
        ("FAILED_RETRYABLE", {"last_error_code": "'PROVIDER_TIMEOUT'", "next_attempt_at": "now() + interval '1 minute'"}),
    ],
    ids=["pending", "generating_active_lease", "generating_expired_lease", "failed_retryable"],
)
def test_gate2_availability_matrix_maps_to_processing(client, pg_conn, unique_user_id, monkeypatch, status, extra):
    """Gate 2 — PENDING, active-lease GENERATING, expired-lease GENERATING
    and FAILED_RETRYABLE all map identically to PROCESSING: the route
    consults only the outbox `status` column (never lease timestamps),
    so an expired lease is not distinguished from an active one at this
    layer (background recovery, not this GET, is what reclaims it)."""
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    # `extra` values are literal SQL fragments (now()/interval expressions
    # for lease timestamps), not bind params, so they're interpolated
    # directly rather than passed as %s placeholders.
    columns = ["paper_trade_id", "user_id", "requested_report_schema_version",
               "requested_calculation_version", "requested_rules_version", "status"] + list(extra.keys())
    static_values = (trade_id, unique_user_id, schema_v, calc_v, rules_v, status)
    pg_conn.execute(
        f"INSERT INTO paper_trade_postmortem_outbox ({', '.join(columns)}) "
        f"VALUES (%s, %s, %s, %s, %s, %s, {', '.join(list(extra.values()))})",
        static_values,
    )

    _forbid_all_generation_calls(monkeypatch)
    before = _snapshot_state(pg_conn, trade_id)
    resp = _current_report(client, unique_user_id, trade_id)
    after = _snapshot_state(pg_conn, trade_id)

    assert resp.status_code == 200
    assert resp.json()["availability"] == "PROCESSING"
    assert resp.headers.get("cache-control") == "private, no-store"
    assert after == before, f"GET must cause zero mutation while outbox status={status}"


@pytest.mark.parametrize(
    "scenario",
    ["not_eligible_open_trade", "not_available_no_outbox", "terminal_failure", "integrity_contradiction_no_report", "ready"],
)
def test_gate2_no_mutation_and_no_forbidden_calls_across_states(client, pg_conn, unique_user_id, monkeypatch, scenario):
    """Gate 2 — exact before/after DB-state snapshot proof (every column,
    not just row counts) AND proof that none of the generation/
    acquisition/claim/recovery entry points are invoked, for every
    remaining governed availability state not already covered by the
    PROCESSING-matrix test above."""
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    if scenario == "not_eligible_open_trade":
        ensure_portfolio(pg_conn, unique_user_id, cash_usd=1_000_000.0)
        trade_id = _buy(client, unique_user_id).json()["trade_id"]
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        expected_availability = "NOT_ELIGIBLE"
    else:
        trade_id = _open_and_close(client, pg_conn, unique_user_id)
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        schema_v, calc_v, rules_v = current_target_identity(
            base_calculation_version=CALCULATION_VERSION,
            numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
        )
        if scenario == "not_available_no_outbox":
            expected_availability = "NOT_AVAILABLE"
        elif scenario == "terminal_failure":
            _insert_outbox(pg_conn, trade_id=trade_id, user_id=unique_user_id,
                            schema_v=schema_v, calc_v=calc_v, rules_v=rules_v, status="FAILED_TERMINAL")
            expected_availability = "TERMINAL_FAILURE"
        elif scenario == "integrity_contradiction_no_report":
            pg_conn.execute(
                """INSERT INTO paper_trade_postmortem_outbox
                   (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
                    requested_rules_version, status, completed_at)
                   VALUES (%s, %s, %s, %s, %s, 'COMPLETE', now())""",
                (trade_id, unique_user_id, schema_v, calc_v, rules_v),
            )
            expected_availability = "INTEGRITY_CONTRADICTION"
        elif scenario == "ready":
            report_store.persist_report(
                pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
                report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
                report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
                evidence_bundle_version="1.2.0-gate2-ready", status="COMPLETE",
                structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
                source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
            )
            expected_availability = "READY"

    _forbid_all_generation_calls(monkeypatch)
    before = _snapshot_state(pg_conn, trade_id)
    resp = _current_report(client, unique_user_id, trade_id)
    after = _snapshot_state(pg_conn, trade_id)

    assert resp.status_code == 200
    assert resp.json()["availability"] == expected_availability
    assert after == before, f"GET must cause zero mutation in scenario={scenario}"


def test_gate2_ready_response_serialization_is_deterministic(client, pg_conn, unique_user_id, monkeypatch):
    """Deterministic-serialization proof: two consecutive GETs of the
    same persisted READY report return byte-identical response bodies
    (not merely equal-after-parsing) — proving the response is built
    solely from persisted, immutable content with no nondeterministic
    ordering, timestamp, or identity substitution across repeated
    serialization of the SAME underlying row."""
    from services.postmortem import report_store
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION

    trade_id = _open_and_close(client, pg_conn, unique_user_id)
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="1.2.0-gate2-determinism", status="COMPLETE",
        structured_report=_VALID_STRUCTURED_REPORT, evidence_items=[], claims=[],
        source_manifest=_VALID_MANIFEST, evidence_gaps=[], warnings=[],
    )

    bodies = [_current_report(client, unique_user_id, trade_id).content for _ in range(5)]
    assert all(b == bodies[0] for b in bodies), "Repeated GETs of the same persisted report must be byte-identical."
