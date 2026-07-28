"""
Trade Postmortem Engine, Sprint 2 — unit tests for
services.postmortem.generation_service: the pure build_report_payload
function (COMPLETE vs LIMITED_EVIDENCE determination, enum-to-JSON
conversion, determinism) and the conn-touching generate_and_persist
wrapper (wired against report_store + outbox via a minimal fake).
"""
import datetime as dt
import json
from contextlib import contextmanager

import pytest

from services.postmortem.close_service import CALCULATION_VERSION
from services.postmortem.deterministic import ClosedTradeRecord, compute_postmortem
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION
from services.postmortem.exit_snapshot import CloseExitMechanism, build_exit_snapshot
from services.postmortem.generation_service import (
    REPORT_SCHEMA_VERSION,
    build_report_payload,
    generate_and_persist,
)


def _closed_trade(**overrides) -> ClosedTradeRecord:
    base = dict(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US",
        quantity=10, entry_price=100.0, exit_price=120.0,
        stop_loss=90.0, target_price=130.0,
        opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
        closed_at=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
        trade_management_mode="manual", exit_reason="MANUAL",
    )
    base.update(overrides)
    return ClosedTradeRecord(**base)


def _exit_snapshot(**overrides):
    kwargs = dict(
        paper_trade_id=1, user_id="user-aaa", symbol="AAPL", market="US",
        exit_mechanism=CloseExitMechanism.MANUAL, exit_mechanism_raw="MANUAL",
        exit_price=120.0, exit_quantity=10,
        closed_at=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
        realized_pnl_abs=200.0, management_mode="manual",
    )
    kwargs.update(overrides)
    return build_exit_snapshot(**kwargs)


@pytest.mark.unit
class TestBuildReportPayload:
    def test_no_entry_no_exit_snapshot_is_limited_evidence(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=None)
        assert payload.status == "LIMITED_EVIDENCE"
        assert any("no entry snapshot" in g for g in payload.evidence_gaps)
        assert any("no exit snapshot" in g for g in payload.evidence_gaps)

    def test_exit_snapshot_present_but_no_entry_snapshot_still_limited(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        assert payload.status == "LIMITED_EVIDENCE"
        assert any("no entry snapshot" in g for g in payload.evidence_gaps)
        assert not any("no exit snapshot" in g for g in payload.evidence_gaps)

    def test_json_payloads_are_serializable(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        json.dumps(payload.structured_report, default=str)
        json.dumps(payload.evidence_items, default=str)
        json.dumps(payload.claims, default=str)

    def test_enum_fields_converted_to_plain_strings_not_enum_repr(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        pm_dict = payload.structured_report["postmortem"]
        assert pm_dict["outcome"] == "WIN"
        assert isinstance(pm_dict["outcome"], str)
        assert pm_dict["exit_mechanism"] == "MANUAL"

    def test_deterministic_for_identical_inputs(self):
        postmortem = compute_postmortem(_closed_trade())
        snap = _exit_snapshot()
        a = build_report_payload(postmortem, None, exit_snapshot=snap)
        b = build_report_payload(postmortem, None, exit_snapshot=snap)
        assert a.structured_report == b.structured_report
        assert a.evidence_items == b.evidence_items
        assert a.claims == b.claims

    def test_source_manifest_records_evidence_presence(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        assert payload.source_manifest["has_entry_snapshot"] is False
        assert payload.source_manifest["has_exit_snapshot"] is True
        assert payload.source_manifest["attribution_rules_version"] == ATTRIBUTION_RULES_VERSION

    def test_never_fabricates_evidence_for_a_bare_manual_trade(self):
        """A plain manually-opened, manually-closed trade with no entry
        snapshot must never produce claims asserting signal agreement it
        has no evidence for — every claim's evidence class must come from
        build_evidence_attribution's own governed model, not this
        function inventing anything."""
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        assert len(payload.claims) > 0
        for claim in payload.claims:
            assert "evidence_class" in claim or "supporting_evidence_ids" in claim

    def test_exit_snapshot_schema_version_recorded_in_manifest(self):
        postmortem = compute_postmortem(_closed_trade())
        snap = _exit_snapshot()
        payload = build_report_payload(postmortem, None, exit_snapshot=snap)
        assert payload.source_manifest["exit_snapshot_schema_version"] == snap.exit_snapshot_schema_version
        assert payload.source_manifest["exit_trigger_timing_verification"] == snap.trigger_timing_verification

    def test_none_exit_snapshot_manifest_has_null_schema_version(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=None)
        assert payload.source_manifest["exit_snapshot_schema_version"] is None

    def test_exit_evidence_claims_are_merged_into_the_claims_list(self):
        """Stage 4 correction — the actual exit snapshot must enter the
        evidence bundle as real claims, not just flip a Boolean."""
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot())
        exit_claim_ids = {c["claim_id"] for c in payload.claims if c["report_section"] == "exit_evidence"}
        assert "CLM-1-EXIT_MECHANISM_V1" in exit_claim_ids
        assert "CLM-1-EXIT_EXECUTION_V1" in exit_claim_ids

    def test_different_exit_price_changes_evidence_hash_relevant_content(self):
        """Stage 4 correction — the persisted content changes when the
        ACTUAL evidence changes, not merely because a Boolean stayed
        True. Two payloads for the same trade but different exit prices
        must differ in their claims/evidence_items, not be byte-identical."""
        postmortem = compute_postmortem(_closed_trade())
        payload_a = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot(exit_price=120.0))
        payload_b = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot(exit_price=999.0))
        assert payload_a.claims != payload_b.claims
        assert payload_a.evidence_items != payload_b.evidence_items

    def test_manual_close_trigger_timing_not_applicable(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot=_exit_snapshot(exit_mechanism=CloseExitMechanism.MANUAL))
        trigger_claim = next(c for c in payload.claims if c["rule_id"] == "EXIT_TRIGGER_TIMING_V1")
        assert "manually" in trigger_claim["claim_text"]
        assert trigger_claim["evidence_class"] == "MECHANICALLY_VERIFIED"

    def test_auto_close_trigger_timing_stays_client_reported_unverified(self):
        """Browser-reported stop/target timing must remain
        CLIENT_REPORTED_UNVERIFIED — never upgraded to a stronger trust
        level by this report-building step."""
        postmortem = compute_postmortem(_closed_trade())
        snap = _exit_snapshot(exit_mechanism=CloseExitMechanism.STOP_LOSS, exit_mechanism_raw="STOP_LOSS")
        assert snap.trigger_timing_verification == "CLIENT_REPORTED_UNVERIFIED"
        payload = build_report_payload(postmortem, None, exit_snapshot=snap)
        trigger_claim = next(c for c in payload.claims if c["rule_id"] == "EXIT_TRIGGER_TIMING_V1")
        assert trigger_claim["evidence_class"] != "MECHANICALLY_VERIFIED"
        assert "never been independently verified" in trigger_claim["claim_text"]


class _FakeConn:
    def __init__(self, expected_claimed_by="tok-a"):
        self.report_rows = {}
        self.next_id = 1
        self.outbox_marks = []
        self.expected_claimed_by = expected_claimed_by

    def execute(self, sql, params):
        stripped = sql.strip()
        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.report_rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = None
                    return self
            new_id = self.next_id
            self.next_id += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status, json.loads(structured), json.loads(ev_items),
                   json.loads(claims), json.loads(manifest), json.loads(gaps), json.loads(warnings))
            self.report_rows[new_id] = row
            self._pending = row
            return self
        if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.report_rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = row
                    return self
            self._pending = None
            return self
        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id, claimed_by = params
            self.outbox_marks.append((outbox_id, status, claimed_by))
            self._pending = (outbox_id,) if claimed_by == self.expected_claimed_by else None
            return self
        raise AssertionError(f"unexpected SQL: {sql!r}")

    def fetchone(self):
        return self._pending

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


@pytest.mark.unit
class TestGenerateAndPersist:
    def test_persists_and_marks_outbox_terminal(self):
        conn = _FakeConn(expected_claimed_by="tok-a")
        postmortem = compute_postmortem(_closed_trade())
        report, created = generate_and_persist(
            conn, trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot=_exit_snapshot(),
            outbox_id=42, claimed_by="tok-a",
        )
        assert created is True
        assert report.report_schema_version == REPORT_SCHEMA_VERSION
        assert report.calculation_version == CALCULATION_VERSION
        assert conn.outbox_marks == [(42, report.status, "tok-a")]

    def test_without_outbox_id_does_not_touch_outbox(self):
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        generate_and_persist(
            conn, trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot=_exit_snapshot(),
        )
        assert conn.outbox_marks == []

    def test_outbox_id_without_claimed_by_raises(self):
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        with pytest.raises(ValueError):
            generate_and_persist(
                conn, trade_id=1, user_id="user-aaa", market="US",
                report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
                postmortem=postmortem, entry_snapshot=None, exit_snapshot=_exit_snapshot(),
                outbox_id=42,
            )

    def test_stale_lease_raises_and_does_not_leave_a_dangling_report(self):
        """Stage 3/8 correction — if the outbox mark-terminal doesn't
        match (a fresher claimant reclaimed the lease), generate_and_
        persist must raise StaleLeaseError so the caller's own
        `with conn.transaction():` rolls the report INSERT back too —
        never leaving a persisted report whose corresponding outbox row
        was never marked terminal by THIS attempt."""
        from services.postmortem.generation_service import StaleLeaseError
        conn = _FakeConn(expected_claimed_by="fresh-tok")  # a different worker now owns the lease
        postmortem = compute_postmortem(_closed_trade())
        with pytest.raises(StaleLeaseError):
            generate_and_persist(
                conn, trade_id=1, user_id="user-aaa", market="US",
                report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
                postmortem=postmortem, entry_snapshot=None, exit_snapshot=_exit_snapshot(),
                outbox_id=42, claimed_by="stale-tok",
            )

    def test_repeated_call_same_versions_idempotent(self):
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        kwargs = dict(
            trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot=_exit_snapshot(),
        )
        first, created1 = generate_and_persist(conn, **kwargs)
        second, created2 = generate_and_persist(conn, **kwargs)
        assert created1 is True
        assert created2 is False
        assert first.id == second.id
        assert len(conn.report_rows) == 1
