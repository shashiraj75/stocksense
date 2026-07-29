"""
Trade Postmortem Sprint 3A, Stage H/I — unit tests for
services.postmortem.price_path_generation: the five-phase orchestration
service tying persisted price-path evidence into postmortem report
generation, and immutable report supersession.

All database interaction is via hand-written fake connections — no real
PostgreSQL, no live network call anywhere in this file.
"""
import dataclasses
import datetime as dt
import inspect

import pytest

from services.market_hours import ET
from services.postmortem import price_path_generation as ppg
from services.postmortem.generation_service import StaleLeaseError
from services.postmortem.price_path_acquisition import build_price_path_evidence
from services.postmortem.price_path_store import persist_evidence
from services.postmortem.report_store import PersistedReport


# --------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------

class _EvidenceFakeConn:
    """Backs price_path_store.get_current_evidence / persist_evidence."""

    def __init__(self):
        self.rows = {}
        self.next_id = 1

    def execute(self, sql, params):
        import json
        stripped = sql.strip()
        if stripped.startswith("INSERT INTO paper_trade_price_path_evidence"):
            key = (params[0], params[4], params[5], params[7])
            for row in self.rows.values():
                if (row[1], row[5], row[6], row[8]) == key:
                    self._pending = None
                    return self
            # Real JSONB columns auto-deserialize on the driver side —
            # simulate that here for source_manifest/limitations/bars
            # (params[-4], params[-3], params[-2] — evidence_hash at
            # params[-1] is a plain string, per price_path_store.
            # persist_evidence's own param order) rather than storing
            # raw JSON strings a real PersistedPricePathEvidence would
            # never actually contain.
            params = list(params)
            for idx in (-4, -3, -2):
                params[idx] = json.loads(params[idx])
            params = tuple(params)
            new_id = self.next_id
            self.next_id += 1
            row = (new_id,) + params
            self.rows[new_id] = row
            self._pending = row
            return self
        if stripped.startswith("SELECT") and "user_id = %s" in sql:
            paper_trade_id, user_id, version, source_id, source_version = params
            key = (paper_trade_id, version, source_id, source_version)
            for row in self.rows.values():
                if (row[1], row[5], row[6], row[8]) == key and row[2] == user_id:
                    self._pending = row
                    return self
            self._pending = None
            return self
        if stripped.startswith("SELECT"):
            paper_trade_id, version, source_id, source_version = params
            key = (paper_trade_id, version, source_id, source_version)
            for row in self.rows.values():
                if (row[1], row[5], row[6], row[8]) == key:
                    self._pending = row
                    return self
            self._pending = None
            return self
        raise AssertionError(f"unexpected SQL in _EvidenceFakeConn: {sql!r}")

    def fetchone(self):
        return self._pending


class _ReportFakeConn:
    """Backs report_store.persist_report / outbox.mark_terminal, mirroring
    the existing fake-conn pattern in test_postmortem_generation_service.py."""

    def __init__(self, expected_claimed_by=None, lease_valid=True):
        self.rows = {}
        self.next_id = 1
        self.outbox_marks = []
        self.expected_claimed_by = expected_claimed_by
        self.lease_valid = lease_valid

    def execute(self, sql, params):
        import json
        stripped = sql.strip()
        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, supersedes_id) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = None
                    return self
            new_id = self.next_id
            self.next_id += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status, json.loads(structured), json.loads(ev_items),
                   json.loads(claims), json.loads(manifest), json.loads(gaps), json.loads(warnings), supersedes_id)
            self.rows[new_id] = row
            self._pending = row
            return self
        if stripped.startswith("SELECT") and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in self.rows.values():
                if (row[1], row[6], row[7], row[8]) == key:
                    self._pending = row
                    return self
            self._pending = None
            return self
        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id, claimed_by = params
            self.outbox_marks.append((outbox_id, status, claimed_by))
            ok = self.lease_valid and (self.expected_claimed_by is None or claimed_by == self.expected_claimed_by)
            self._pending = (outbox_id,) if ok else None
            return self
        raise AssertionError(f"unexpected SQL in _ReportFakeConn: {sql!r}")

    def fetchone(self):
        return self._pending


def _bundle(paper_trade_id=1, **overrides):
    kwargs = dict(
        paper_trade_id=paper_trade_id, user_id="user-aaa", symbol="AAPL", market="US",
        market_timezone_name="America/New_York", market_tzinfo=ET,
        entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
        exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET),
        raw_bars=[
            {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 115.0, "low": 99.0, "close": 110.0, "volume": 500, "adj_close": None, "dividend": 0.0},
            {"date": dt.date(2026, 6, 3), "open": 110.0, "high": 112.0, "low": 90.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        ],
        split_events=[],
    )
    kwargs.update(overrides)
    return build_price_path_evidence(**kwargs)


def _prior_report(**overrides):
    kwargs = dict(
        id=42, paper_trade_id=1, user_id="user-aaa", market="US",
        report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York",
        report_schema_version="1.0.0", calculation_version="1.0.0", attribution_rules_version="1.0.0",
        evidence_bundle_version="1.0.0", evidence_hash="deadbeef", status="COMPLETE",
        structured_report={"trade_id": 1}, evidence_items=[], claims=[], source_manifest={},
        evidence_gaps=[], warnings=[], supersedes_report_id=None,
    )
    kwargs.update(overrides)
    return PersistedReport(**kwargs)


# --------------------------------------------------------------------
# Phase 1
# --------------------------------------------------------------------

@pytest.mark.unit
class TestPhase1LoadGenerationContext:
    def test_finds_no_compatible_evidence_when_none_persisted(self):
        conn = _EvidenceFakeConn()
        ctx = ppg.load_generation_context(
            conn, trade_id=1, user_id="user-aaa", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET), level_history_complete=True, prior_report=None,
        )
        assert ctx.compatible_evidence is None

    def test_finds_compatible_evidence_when_already_persisted(self):
        conn = _EvidenceFakeConn()
        persist_evidence(conn, _bundle())
        ctx = ppg.load_generation_context(
            conn, trade_id=1, user_id="user-aaa", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET), level_history_complete=True, prior_report=None,
        )
        assert ctx.compatible_evidence is not None

    def test_user_scoped_evidence_lookup(self):
        conn = _EvidenceFakeConn()
        persist_evidence(conn, _bundle())
        ctx = ppg.load_generation_context(
            conn, trade_id=1, user_id="attacker", symbol="AAPL", market="US",
            market_timezone_name="America/New_York", entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET),
            entry_price=100.0, exit_price=110.0, applicable_stop=90.0, applicable_target=120.0,
            exit_timestamp=dt.datetime(2026, 6, 5, tzinfo=ET), level_history_complete=True, prior_report=None,
        )
        assert ctx.compatible_evidence is None


# --------------------------------------------------------------------
# Phase 2
# --------------------------------------------------------------------

@pytest.mark.unit
class TestPhase2NoDatabaseConnection:
    def test_acquire_evidence_outside_transaction_accepts_no_connection_parameter(self):
        params = inspect.signature(ppg.acquire_evidence_outside_transaction).parameters
        assert "conn" not in params
        assert "connection" not in params
        assert "cursor" not in params

    def test_acquire_evidence_outside_transaction_uses_injected_fakes_only(self):
        calls = {"bars": 0}

        def fake_bars(symbol, start, end):
            calls["bars"] += 1
            return [{"date": dt.date(2026, 6, 2), "open": 100.0, "high": 105.0, "low": 99.0, "close": 101.0, "volume": 500}]

        ctx = ppg.GenerationContext(
            trade_id=1, user_id="u", symbol="AAPL", market="US", market_timezone_name="America/New_York",
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), entry_price=100.0, exit_price=101.0,
            applicable_stop=90.0, applicable_target=120.0, exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            level_history_complete=True, prior_report=None, compatible_evidence=None,
        )
        bundle = ppg.acquire_evidence_outside_transaction(
            ctx, market_tzinfo=ET, fetch_bars_fn=fake_bars, fetch_splits_fn=lambda *a: [], fetch_dividends_fn=lambda *a: [],
        )
        assert calls["bars"] == 1
        assert bundle.paper_trade_id == 1


# --------------------------------------------------------------------
# Phase 3
# --------------------------------------------------------------------

@pytest.mark.unit
class TestPhase3PersistEvidence:
    def test_idempotent_persist(self):
        conn = _EvidenceFakeConn()
        bundle = _bundle()
        first, created1 = ppg.persist_price_path_evidence(conn, bundle)
        second, created2 = ppg.persist_price_path_evidence(conn, bundle)
        assert created1 is True and created2 is False
        assert first.id == second.id


# --------------------------------------------------------------------
# Phase 4
# --------------------------------------------------------------------

@pytest.mark.unit
class TestPhase4BuildPayload:
    def _persisted_evidence(self):
        conn = _EvidenceFakeConn()
        evidence, _ = ppg.persist_price_path_evidence(conn, _bundle())
        return evidence

    def test_payload_complete_status_and_populated_fields(self):
        evidence = self._persisted_evidence()
        payload = ppg.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=95.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=1,
        )
        assert payload.price_path_section["mfe_abs"] == pytest.approx(15.0)  # interior bar high 115 - entry 100
        assert payload.price_path_section["touch_order"] is not None
        assert len(payload.evidence_items) > 0
        assert len(payload.claims) == 3  # MFE, MAE, TOUCH_ORDER

    def test_no_interior_bars_yields_insufficient_evidence_claims(self):
        conn = _EvidenceFakeConn()
        same_day_bundle = _bundle(
            entry_timestamp=dt.datetime(2026, 6, 2, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 2, tzinfo=ET),
            raw_bars=[{"date": dt.date(2026, 6, 2), "open": 100.0, "high": 105.0, "low": 98.0, "close": 101.0, "volume": 500, "adj_close": None, "dividend": 0.0}],
        )
        evidence, _ = ppg.persist_price_path_evidence(conn, same_day_bundle)
        payload = ppg.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=101.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=1,
        )
        from services.postmortem.evidence import INSUFFICIENT_EVIDENCE_SENTENCE
        mfe_claim = next(c for c in payload.claims if c["factor"] == "mfe")
        assert mfe_claim["claim_text"] == INSUFFICIENT_EVIDENCE_SENTENCE

    def test_both_same_bar_ambiguous_touch_order_downgrades_status_despite_complete_bundle(self):
        """Stage J-F5 — a bundle can be data_completeness=COMPLETE with a
        fully EXCURSION_COMPLETE MFE/MAE and STILL have an ambiguous touch
        order (both stop and target crossed within one interior daily
        bar). The report ceiling must reflect that ambiguity rather than
        report COMPLETE while its own touch_order claim is
        INSUFFICIENT_EVIDENCE-flavored — see build_price_path_report_
        payload's _AMBIGUOUS_TOUCH_ORDERS set."""
        conn = _EvidenceFakeConn()
        # entry 6/1, exit 6/5; interior bar 6/3 has low=85 (below stop=90)
        # AND high=125 (above target=120) — both crossed in the same bar.
        bundle = _bundle(raw_bars=[
            {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 108.0, "low": 99.0, "close": 105.0, "volume": 500, "adj_close": None, "dividend": 0.0},
            {"date": dt.date(2026, 6, 3), "open": 105.0, "high": 125.0, "low": 85.0, "close": 100.0, "volume": 500, "adj_close": None, "dividend": 0.0},
        ])
        evidence, _ = ppg.persist_price_path_evidence(conn, bundle)
        payload = ppg.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=100.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=1,
        )
        from services.postmortem.price_path_calculator import BOTH_SAME_BAR_AMBIGUOUS

        assert payload.price_path_section["touch_order"] == BOTH_SAME_BAR_AMBIGUOUS
        assert payload.status == "LIMITED_EVIDENCE"
        # The ambiguity must not suppress independently valid MFE/MAE —
        # those are computed from `excursion`, independent of touch order,
        # and remain real numbers, not nulled out.
        assert payload.price_path_section["mfe_abs"] == pytest.approx(25.0)  # interior high 125 - entry 100
        assert payload.price_path_section["mae_signed_abs"] == pytest.approx(-15.0)  # interior low 85 - entry 100

    def test_clean_stop_only_touch_order_does_not_downgrade_status(self):
        """Control case — STOP_ONLY is a definitive, non-ambiguous claim
        and must not trigger the Stage J-F5 downgrade. Uses a fully
        COMPLETE bundle (every session in [entry, exit] observed) so the
        only variable under test is touch-order ambiguity, not an
        unrelated PARTIAL-bundle ceiling."""
        conn = _EvidenceFakeConn()
        bundle = _bundle(
            entry_timestamp=dt.datetime(2026, 6, 1, tzinfo=ET), exit_timestamp=dt.datetime(2026, 6, 3, tzinfo=ET),
            raw_bars=[
                {"date": dt.date(2026, 6, 1), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": dt.date(2026, 6, 2), "open": 100.0, "high": 108.0, "low": 85.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
                {"date": dt.date(2026, 6, 3), "open": 95.0, "high": 96.0, "low": 94.0, "close": 95.0, "volume": 500, "adj_close": None, "dividend": 0.0},
            ],
        )
        assert bundle.data_completeness == "COMPLETE"
        evidence, _ = ppg.persist_price_path_evidence(conn, bundle)
        payload = ppg.build_price_path_report_payload(
            evidence, entry_price=100.0, exit_price=95.0, applicable_stop=90.0, applicable_target=120.0,
            level_history_complete=True, trade_id=1,
        )
        from services.postmortem.price_path_calculator import EXCURSION_COMPLETE, STOP_ONLY

        assert payload.price_path_section["touch_order"] == STOP_ONLY
        assert payload.price_path_section["price_path_status"] == "COMPLETE"
        assert payload.status == "COMPLETE"


# --------------------------------------------------------------------
# Phase 5 / Stage I supersession
# --------------------------------------------------------------------

@pytest.mark.unit
class TestPhase5PersistReportAndSupersession:
    def _payload(self):
        return ppg.PricePathReportPayload(
            status="COMPLETE", price_path_section={"price_path_status": "COMPLETE"},
            evidence_items=[], claims=[], limitations=[],
        )

    def test_creates_new_row_distinct_from_prior_report(self):
        conn = _ReportFakeConn()
        prior = _prior_report()
        report, created = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert created is True
        assert report.report_schema_version == ppg.PRICE_PATH_REPORT_SCHEMA_VERSION
        assert report.report_schema_version != prior.report_schema_version
        assert report.id != prior.id

    def test_supersedes_report_id_points_to_prior_report(self):
        conn = _ReportFakeConn()
        prior = _prior_report()
        report, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert report.supersedes_report_id == prior.id

    def test_prior_report_object_never_mutated(self):
        conn = _ReportFakeConn()
        prior = _prior_report()
        before = dataclasses.asdict(prior)
        ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert dataclasses.asdict(prior) == before

    def test_prior_report_row_remains_independently_readable(self):
        """The fake conn's own row store simulates the DB table — the
        prior report's row is never deleted or overwritten by the new
        INSERT, since it lives under a different (schema_version,
        calc_version) key."""
        conn = _ReportFakeConn()
        prior = _prior_report()
        conn.rows[prior.id] = (
            prior.id, prior.paper_trade_id, prior.user_id, prior.market, prior.report_trading_date,
            prior.market_timezone, prior.report_schema_version, prior.calculation_version,
            prior.attribution_rules_version, prior.evidence_bundle_version, prior.evidence_hash, prior.status,
            prior.structured_report, prior.evidence_items, prior.claims, prior.source_manifest,
            prior.evidence_gaps, prior.warnings, prior.supersedes_report_id,
        )
        ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert prior.id in conn.rows
        assert conn.rows[prior.id][6] == "1.0.0"  # report_schema_version column unchanged

    def test_identical_replay_returns_existing_row_not_a_duplicate(self):
        conn = _ReportFakeConn()
        prior = _prior_report()
        first, created1 = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        second, created2 = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert created1 is True
        assert created2 is False
        assert first.id == second.id

    def test_changed_source_version_creates_distinct_report_identity(self):
        conn = _ReportFakeConn()
        prior = _prior_report()
        first, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        second, created2 = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="2.0.0",
        )
        assert created2 is True
        assert second.id != first.id
        assert second.calculation_version != first.calculation_version

    def test_settles_outbox_in_same_call(self):
        conn = _ReportFakeConn(expected_claimed_by="tok-a")
        prior = _prior_report()
        ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
            outbox_id=99, claimed_by="tok-a",
        )
        assert conn.outbox_marks == [(99, "COMPLETE", "tok-a")]

    def test_stale_lease_raises_and_report_was_already_inserted_in_this_attempt(self):
        """Documents the exact rollback contract: this function itself
        raises StaleLeaseError; the CALLER's own `with conn.transaction():`
        block (not exercised by this fake conn) is what rolls the
        report INSERT back too — matching generate_and_persist's
        identical, already-tested contract."""
        conn = _ReportFakeConn(expected_claimed_by="tok-a", lease_valid=False)
        prior = _prior_report()
        with pytest.raises(StaleLeaseError):
            ppg.persist_price_path_report(
                conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
                report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
                outbox_id=99, claimed_by="tok-a",
            )

    def test_lower_status_never_upgraded_by_price_path_layer(self):
        conn = _ReportFakeConn()
        prior = _prior_report(status="LIMITED_EVIDENCE")
        report, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert report.status == "LIMITED_EVIDENCE"

    def test_trade_context_ceiling_alone_caps_an_otherwise_complete_outcome(self):
        """Stage J3 wiring proof — a COMPLETE prior report AND a COMPLETE
        price-path payload must still settle LIMITED_EVIDENCE when the
        Stage J1A trade-context ceiling (computed at eligibility time,
        BEFORE acquisition ever ran) says LIMITED_EVIDENCE. Before this
        wiring, trade_context_ceiling was computed but never consulted
        here -- only used to gate whether acquisition ran at all."""
        conn = _ReportFakeConn()
        prior = _prior_report(status="COMPLETE")
        report, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
            trade_context_ceiling="LIMITED_EVIDENCE",
        )
        assert report.status == "LIMITED_EVIDENCE"

    def test_trade_context_ceiling_complete_does_not_by_itself_upgrade_status(self):
        conn = _ReportFakeConn()
        prior = _prior_report(status="LIMITED_EVIDENCE")
        report, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
            trade_context_ceiling="COMPLETE",
        )
        assert report.status == "LIMITED_EVIDENCE"

    def test_default_trade_context_ceiling_preserves_prior_behavior(self):
        """Callers that don't pass trade_context_ceiling (the default,
        'COMPLETE') are unaffected -- non-breaking for any caller not
        yet updated to compute and pass it."""
        conn = _ReportFakeConn()
        prior = _prior_report(status="COMPLETE")
        report, _ = ppg.persist_price_path_report(
            conn, prior_report=prior, payload=self._payload(), trade_id=1, user_id="user-aaa", market="US",
            report_trading_date=dt.date(2026, 6, 5), market_timezone="America/New_York", source_version="1.0.0",
        )
        assert report.status == "COMPLETE"
