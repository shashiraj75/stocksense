"""
DP-025 foundation — CLI smoke tests for services/validation/pipeline_replay.py's
optional local CLI. Confirms: requires an explicit fixture path, defaults to
strict mode, writes only to stdout or a user-specified local path, performs
no persistence.
"""

import json

import pytest

from services.validation.pipeline_replay import main


def _write_fixture(tmp_path, mode="diagnostic", integrity_ok=False):
    fixture = {
        "snapshot_id": "cli-test", "as_of": "2026-01-01T00:00:00Z", "market": "US", "horizon": "medium",
        "source": "manual_fixture", "replay_mode": mode,
        "candidates": [
            {"symbol": "AAA", "signal": "BUY", "confidence": 70, "technical_score": 60.0,
             "fundamental_score": 55.0, "quality_score": 0.0, "sentiment_score": None,
             "sentiment_available": False, "quality_available": True, "reasoning": [], "cap_tier": "large"},
            {"symbol": "BBB", "signal": "BUY", "confidence": 60, "technical_score": 40.0,
             "fundamental_score": 80.0, "quality_score": 52.0, "sentiment_score": 60.0,
             "sentiment_available": True, "quality_available": True, "reasoning": [], "cap_tier": "mid"},
        ],
        "regime_id": 0, "regime_label": "BULL_CALM", "regime_weight_multipliers": {},
        "ic_weights": {"tech": 0.3, "fund": 0.3, "sentiment": 0.2, "quality": 0.2},
    }
    if integrity_ok:
        fixture["point_in_time_integrity"] = {
            "provenance": "point_in_time", "timestamp": "point_in_time",
            "technical": "point_in_time", "fundamental": "point_in_time", "quality": "point_in_time",
        }
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    return path


class TestCliRequiresExplicitFixture:
    def test_no_fixture_argument_exits_nonzero(self):
        with pytest.raises(SystemExit):
            main([])


class TestCliDefaultsToStrict:
    def test_default_mode_refuses_a_non_point_in_time_fixture(self, tmp_path, capsys):
        path = _write_fixture(tmp_path, mode="diagnostic", integrity_ok=False)
        exit_code = main([str(path)])
        assert exit_code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "refused_incomplete"

    def test_explicit_diagnostic_mode_permits_incomplete_fixture(self, tmp_path, capsys):
        path = _write_fixture(tmp_path, mode="diagnostic", integrity_ok=False)
        exit_code = main([str(path), "--mode", "diagnostic"])
        assert exit_code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "diagnostic_completed"
        assert any("DIAGNOSTIC ONLY" in w for w in out["warnings"])


class TestCliOutputPath:
    def test_output_flag_writes_to_specified_file_not_stdout(self, tmp_path, capsys):
        fixture_path = _write_fixture(tmp_path, mode="diagnostic", integrity_ok=True)
        output_path = tmp_path / "result.json"
        exit_code = main([str(fixture_path), "--mode", "diagnostic", "--output", str(output_path)])
        assert exit_code == 0
        assert output_path.exists()
        result = json.loads(output_path.read_text())
        assert result["status"] == "diagnostic_completed"
        assert "result_hash" in result
        # nothing printed to stdout when --output is used
        assert capsys.readouterr().out == ""
