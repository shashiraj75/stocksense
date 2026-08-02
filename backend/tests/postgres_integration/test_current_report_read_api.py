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
    real_row = pg_conn.execute(
        """SELECT structured_report, evidence_items, claims, source_manifest, evidence_gaps, warnings, status
           FROM paper_trade_postmortem_report WHERE paper_trade_id = %s""",
        (real_trade_id,),
    ).fetchone()
    (real_structured, real_evidence_items, real_claims, real_source_manifest,
     real_evidence_gaps, real_warnings, real_status) = real_row

    schema_v, calc_v, rules_v = current_target_identity(
        base_calculation_version=CALCULATION_VERSION,
        numerical_rules_version=PRICE_PATH_CALC_RULES_VERSION, source_version=SOURCE_VERSION,
    )

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
        "some_future_field_not_yet_modelled": "marker-future-value",
    }
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="source-manifest-marker-test", status="COMPLETE",
        structured_report={"marker": "source-manifest-marker-test"}, evidence_items=[], claims=[],
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
        # price_path_calculation_version intentionally omitted entirely.
    }
    report_store.persist_report(
        pg_conn, paper_trade_id=trade_id, user_id=unique_user_id, market="US",
        report_trading_date=datetime.now(timezone.utc).date(), market_timezone="America/New_York",
        report_schema_version=schema_v, calculation_version=calc_v, attribution_rules_version=rules_v,
        evidence_bundle_version="historical-absence-test", status="COMPLETE",
        structured_report={"marker": "historical-absence-test"}, evidence_items=[], claims=[],
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
        "source": "marker-source", "source_type": "marker-source-type",
        "verification_level": "marker-verification-level", "freshness_status": "marker-freshness-status",
        "limitations": ["marker-limitation-1"],
    }
    marker_claim = {
        "claim_id": "CLM-marker-id", "report_section": "marker-section", "factor": "marker-factor",
        "claim_text": "marker claim text", "evidence_class": "SUPPORTING_EVIDENCE",
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
        structured_report={"marker": "claim-evidence-marker-test"},
        evidence_items=[marker_evidence_item], claims=[marker_claim],
        source_manifest={
            "has_entry_snapshot": True, "has_exit_snapshot": False,
            "exit_snapshot_schema_version": None, "exit_trigger_timing_verification": None,
            "exit_evidence_rules_version": "1.0.0", "phase1_calculation_version": "1.0.0",
            "attribution_rules_version": "1.0.0",
        },
        evidence_gaps=["marker-gap"], warnings=["marker-warning"],
    )

    resp = _current_report(client, unique_user_id, trade_id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["availability"] == "READY"
    assert body["claims"] == [marker_claim]
    assert body["evidence_items"] == [marker_evidence_item]
    assert body["evidence_gaps"] == ["marker-gap"]
    assert body["warnings"] == ["marker-warning"]


def test_malformed_claim_missing_rule_id_fails_closed(client, pg_conn, unique_user_id, monkeypatch):
    from services.postmortem.current_report_generation import current_target_identity
    from services.postmortem.deterministic import CALCULATION_VERSION
    from services.postmortem.price_path_generation import PRICE_PATH_CALC_RULES_VERSION, SOURCE_VERSION
    from services.postmortem import report_store

    trade_id = _open_and_close(client, pg_conn, unique_user_id)  # flag OFF at close
    monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
    malformed_claim = {
        "claim_id": "CLM-malformed", "report_section": "x", "factor": "x",
        "claim_text": "x", "evidence_class": "SUPPORTING_EVIDENCE", "confidence_band": "HIGH",
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
        },
        evidence_gaps=[], warnings=[],
    )
    _assert_fails_closed_to_integrity_contradiction(client, unique_user_id, trade_id, ["CLM-malformed"])
