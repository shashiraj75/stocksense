"""
Phase 2 spec §9 — composer unit tests: flag parsing fail-safe matrix,
flag-off no-op by reference, flag-on additive key, fail-open by
reference, input immutability, no PredictionEngine/network invocation.
"""

import copy
import json
import socket

import pytest

import services.research_analyst.research_composer as rc
from services.research_analyst.research_composer import (
    compose_prediction_response_with_research,
    research_analyst_v2_enabled,
)
from tests.fixtures.research_analyst_fixtures import make_predict_result

_FLAG = "RESEARCH_ANALYST_V2_ENABLED"


class TestFlag:
    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
    def test_truthy_values_enable(self, monkeypatch, value):
        monkeypatch.setenv(_FLAG, value)
        assert research_analyst_v2_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "off", "", "garbage", "2"])
    def test_everything_else_disables(self, monkeypatch, value):
        monkeypatch.setenv(_FLAG, value)
        assert research_analyst_v2_enabled() is False

    def test_default_is_disabled(self, monkeypatch):
        monkeypatch.delenv(_FLAG, raising=False)
        assert research_analyst_v2_enabled() is False


class TestComposer:
    def test_flag_off_returns_original_reference_unchanged(self, monkeypatch):
        monkeypatch.delenv(_FLAG, raising=False)
        result = make_predict_result()
        out = compose_prediction_response_with_research(
            result, symbol="RELIANCE", market="IN", horizon="medium")
        assert out is result and "research_report" not in out

    def test_flag_on_returns_new_dict_with_research_report(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "1")
        result = make_predict_result()
        out = compose_prediction_response_with_research(
            result, symbol="RELIANCE", market="IN", horizon="medium")
        assert out is not result
        assert "research_report" in out
        report = out["research_report"]
        assert report["report_contract_version"] == "1.0"
        assert [s["section_id"] for s in report["sections"]] == [
            "executive_snapshot", "current_signal_context", "key_evidence",
            "risks_and_invalidation", "data_availability", "disclaimer",
        ]
        json.dumps(out["research_report"])  # JSON-safe

    def test_input_never_mutated_flag_on(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "1")
        result = make_predict_result(market="US", symbol="AAPL")
        before = copy.deepcopy(result)
        out = compose_prediction_response_with_research(
            result, symbol="AAPL", market="US", horizon="medium")
        assert result == before and "research_report" not in result
        # every pre-existing key is byte-identical in the new dict
        for key in before:
            assert out[key] == before[key]

    def test_fail_open_returns_original_reference_no_key(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "1")
        monkeypatch.setattr(rc, "assemble_research_report",
                            lambda snapshot: (_ for _ in ()).throw(RuntimeError("boom")))
        result = make_predict_result()
        out = compose_prediction_response_with_research(
            result, symbol="RELIANCE", market="IN", horizon="medium")
        assert out is result and "research_report" not in out

    def test_report_rejection_is_fail_open_too(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "1")
        result = make_predict_result()
        del result["generated_at"]  # validator V-1 -> ReportRejection
        out = compose_prediction_response_with_research(
            result, symbol="RELIANCE", market="IN", horizon="medium")
        assert out is result and "research_report" not in out

    def test_scope_mismatch_is_fail_open(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "1")
        result = make_predict_result(market="IN", symbol="RELIANCE")
        out = compose_prediction_response_with_research(
            result, symbol="TCS", market="IN", horizon="medium")
        assert out is result and "research_report" not in out

    def test_never_calls_prediction_engine_or_network(self, monkeypatch):
        import services.prediction_engine as pe

        def _forbidden(*a, **k):
            raise AssertionError("composer must never invoke PredictionEngine or the network")

        monkeypatch.setenv(_FLAG, "1")
        monkeypatch.setattr(pe.PredictionEngine, "predict", _forbidden)
        monkeypatch.setattr(socket, "socket", _forbidden)
        out = compose_prediction_response_with_research(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        assert "research_report" in out
