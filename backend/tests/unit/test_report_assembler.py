"""
Phase 2 spec §9 — assembler tests: determinism, fixed section order,
citation discipline, honest absence, invalid-snapshot refusal, forbidden
advice-token scan, no probabilities, disclaimer always present.
"""

import dataclasses
import json

from services.research_analyst.intelligence_snapshot import compute_snapshot_hash
from services.research_analyst.report_assembler import (
    FORBIDDEN_ADVICE_TOKENS,
    ReportRejection,
    ResearchReport,
    SECTION_ORDER,
    TEXT_ALL_OWNERS_SUPPORTED,
    TEXT_DECISION_UNAVAILABLE,
    TEXT_NO_INVALIDATION,
    assemble_research_report,
    report_to_dict,
)
from services.research_analyst.snapshot_builder import build_live_intelligence_snapshot
from tests.fixtures.research_analyst_fixtures import make_predict_result


def _snap(market="IN", symbol="RELIANCE", horizon="medium", fixture=None):
    return build_live_intelligence_snapshot(
        fixture or make_predict_result(market=market, symbol=symbol, horizon=horizon),
        symbol=symbol, market=market, horizon=horizon)


def _report(**kwargs) -> ResearchReport:
    report = assemble_research_report(_snap(**kwargs))
    assert isinstance(report, ResearchReport)
    return report


def _all_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _all_strings(k)
            yield from _all_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _all_strings(v)


def _section(report, section_id):
    return next(s for s in report.sections if s.section_id == section_id)


class TestDeterminismAndShape:
    def test_identical_snapshot_byte_identical_report(self):
        a = report_to_dict(assemble_research_report(_snap()))
        b = report_to_dict(assemble_research_report(_snap()))
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_six_fixed_sections_in_order(self):
        report = _report()
        assert tuple(s.section_id for s in report.sections) == SECTION_ORDER

    def test_json_safe(self):
        json.dumps(report_to_dict(_report(market="US", symbol="AAPL")))

    def test_envelope_binds_to_snapshot(self):
        snap = _snap()
        report = assemble_research_report(snap)
        assert report.snapshot_id == snap.snapshot_id
        assert report.snapshot_hash == snap.snapshot_hash == compute_snapshot_hash(snap)
        assert report.scope["currency"] == "INR"


class TestCitationDiscipline:
    def test_every_entry_cites_evidence_ids(self):
        report = _report(market="US", symbol="AAPL")
        snap_ids = {i.evidence_id for i in _snap(market="US", symbol="AAPL").evidence_items}
        for section in report.sections:
            for entry in section.entries:
                assert entry["evidence_ids"], f"{section.section_id}: uncited entry {entry['label']!r}"
                for eid in entry["evidence_ids"]:
                    assert eid in snap_ids, f"dangling citation {eid!r}"

    def test_signal_values_verbatim(self):
        report = _report()
        signal_section = _section(report, "current_signal_context")
        by_label = {e["label"]: e["value"] for e in signal_section.entries}
        assert by_label["Current signal"] == "BUY"
        assert by_label["Confidence"] == 68
        assert by_label["Trade levels"]["stop_loss"] == 2755.0


class TestHonestAbsence:
    def test_decision_unavailable_renders_absence_no_substitute(self):
        report = assemble_research_report(_snap(
            fixture={"symbol": "RELIANCE", "market": "IN", "horizon": "medium",
                     "generated_at": "2026-07-08T10:00:00+00:00", "error": "boom"}))
        assert isinstance(report, ResearchReport)
        section = _section(report, "current_signal_context")
        assert section.text == TEXT_DECISION_UNAVAILABLE and not section.entries

    def test_data_availability_preserves_state_distinctions(self):
        fixture = make_predict_result(market="IN")  # FS -> NOT_APPLICABLE structurally
        fixture["business_quality"] = None          # -> UNAVAILABLE
        fixture["valuation_intelligence"] = {"status": "feature_disabled"}
        del fixture["growth_intelligence"]          # -> MISSING
        report = assemble_research_report(_snap(fixture=fixture))
        section = _section(report, "data_availability")
        labels = " | ".join(e["label"] for e in section.entries)
        assert "FinancialStrength: NOT_APPLICABLE" in labels
        assert "BusinessQuality: UNAVAILABLE" in labels
        assert "ValuationIntelligence: FEATURE_DISABLED" in labels
        assert "GrowthIntelligence: MISSING" in labels
        reasons = [e["value"] for e in section.entries]
        assert all(r for r in reasons), "every limitation entry carries the owner reason"

    def test_all_supported_renders_positive_coverage_text(self):
        report = _report(market="US", symbol="AAPL")
        section = _section(report, "data_availability")
        assert section.text == TEXT_ALL_OWNERS_SUPPORTED and not section.entries

    def test_invalidation_always_honest_fixed_text(self):
        for kwargs in ({}, {"market": "US", "symbol": "AAPL"}):
            report = _report(**kwargs)
            assert TEXT_NO_INVALIDATION in _section(report, "risks_and_invalidation").text


class TestRefusal:
    def test_invalid_snapshot_refused_with_rule_ids(self):
        fixture = make_predict_result()
        del fixture["generated_at"]  # validator V-1
        result = assemble_research_report(_snap(fixture=fixture))
        assert isinstance(result, ReportRejection)
        assert "V-1" in result.rule_ids and result.reasons

    def test_tampered_snapshot_refused(self):
        snap = _snap()
        tampered = dataclasses.replace(snap, snapshot_hash="0" * 64)
        result = assemble_research_report(tampered)
        assert isinstance(result, ReportRejection) and "V-7" in result.rule_ids

    def test_non_snapshot_input_refused(self):
        result = assemble_research_report({"not": "a snapshot"})
        assert isinstance(result, ReportRejection)


class TestLanguageSafety:
    def _reports(self):
        yield _report()
        yield _report(market="US", symbol="AAPL")
        degraded = make_predict_result()
        degraded["business_quality"] = None
        degraded["growth_intelligence"] = {"error": "x"}
        yield assemble_research_report(_snap(fixture=degraded))
        yield assemble_research_report(_snap(
            fixture={"symbol": "RELIANCE", "market": "IN", "horizon": "medium",
                     "generated_at": "2026-07-08T10:00:00+00:00", "error": "boom"}))

    def test_no_forbidden_advice_tokens_anywhere(self):
        for report in self._reports():
            for text in _all_strings(report_to_dict(report)):
                lowered = text.lower()
                for token in FORBIDDEN_ADVICE_TOKENS:
                    assert token not in lowered, f"forbidden token {token!r} in {text!r}"

    def test_no_probability_language_or_fields(self):
        for report in self._reports():
            serialized = json.dumps(report_to_dict(report), sort_keys=True).lower()
            assert "probabilit" not in serialized
            for scenario in ("bull_probability", "bear_probability", "base_case"):
                assert scenario not in serialized

    def test_disclaimer_always_present_and_last(self):
        for report in self._reports():
            last = report.sections[-1]
            assert last.section_id == "disclaimer"
            assert "not financial advice" in last.text.lower()
            assert report.disclosures  # user-decision boundary carried through
