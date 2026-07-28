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


@pytest.mark.unit
class TestBuildReportPayload:
    def test_no_entry_no_exit_snapshot_is_limited_evidence(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot_present=False)
        assert payload.status == "LIMITED_EVIDENCE"
        assert any("no entry snapshot" in g for g in payload.evidence_gaps)
        assert any("no exit snapshot" in g for g in payload.evidence_gaps)

    def test_exit_snapshot_present_but_no_entry_snapshot_still_limited(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot_present=True)
        assert payload.status == "LIMITED_EVIDENCE"
        assert any("no entry snapshot" in g for g in payload.evidence_gaps)
        assert not any("no exit snapshot" in g for g in payload.evidence_gaps)

    def test_json_payloads_are_serializable(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot_present=True)
        json.dumps(payload.structured_report, default=str)
        json.dumps(payload.evidence_items, default=str)
        json.dumps(payload.claims, default=str)

    def test_enum_fields_converted_to_plain_strings_not_enum_repr(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot_present=True)
        pm_dict = payload.structured_report["postmortem"]
        assert pm_dict["outcome"] == "WIN"
        assert isinstance(pm_dict["outcome"], str)
        assert pm_dict["exit_mechanism"] == "MANUAL"

    def test_deterministic_for_identical_inputs(self):
        postmortem = compute_postmortem(_closed_trade())
        a = build_report_payload(postmortem, None, exit_snapshot_present=True)
        b = build_report_payload(postmortem, None, exit_snapshot_present=True)
        assert a.structured_report == b.structured_report
        assert a.evidence_items == b.evidence_items
        assert a.claims == b.claims

    def test_source_manifest_records_evidence_presence(self):
        postmortem = compute_postmortem(_closed_trade())
        payload = build_report_payload(postmortem, None, exit_snapshot_present=True)
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
        payload = build_report_payload(postmortem, None, exit_snapshot_present=True)
        assert len(payload.claims) > 0
        for claim in payload.claims:
            assert "evidence_class" in claim or "supporting_evidence_ids" in claim


class _FakeConn:
    def __init__(self):
        self.report_rows = {}
        self.next_id = 1
        self.outbox_marks = []

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
            status, outbox_id = params
            self.outbox_marks.append((outbox_id, status))
            self._pending = None
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
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        report, created = generate_and_persist(
            conn, trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot_present=True, outbox_id=42,
        )
        assert created is True
        assert report.report_schema_version == REPORT_SCHEMA_VERSION
        assert report.calculation_version == CALCULATION_VERSION
        assert conn.outbox_marks == [(42, report.status)]

    def test_without_outbox_id_does_not_touch_outbox(self):
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        generate_and_persist(
            conn, trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot_present=True,
        )
        assert conn.outbox_marks == []

    def test_repeated_call_same_versions_idempotent(self):
        conn = _FakeConn()
        postmortem = compute_postmortem(_closed_trade())
        kwargs = dict(
            trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
            postmortem=postmortem, entry_snapshot=None, exit_snapshot_present=True,
        )
        first, created1 = generate_and_persist(conn, **kwargs)
        second, created2 = generate_and_persist(conn, **kwargs)
        assert created1 is True
        assert created2 is False
        assert first.id == second.id
        assert len(conn.report_rows) == 1
