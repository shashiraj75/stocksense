"""
DP-026 remediation — get_track_record_summary() now forwards the
point-in-time disclosure fields (fundamentals_point_in_time,
fundamentals_point_in_time_coverage_pct, dp026_status) so the frontend can
distinguish a genuinely-remediated US result, a still-contaminated India
result, and a legacy pre-remediation result (predates data_limitations
entirely) from one another.
"""
import pytest

import services.validation_engine as ve


@pytest.mark.unit
class TestTrackRecordSummaryPitFields:
    def test_us_run_with_data_limitations_forwards_pit_fields(self, monkeypatch):
        monkeypatch.setattr(ve, "get_latest_results", lambda horizon, universe: {
            "available": True, "beat_benchmark_pct": 55.0, "buy_hit_rate_pct": 60.0,
            "buy_signals": 100, "run_at": "2026-07-22T00:00:00Z",
            "data_limitations": {
                "fundamentals_point_in_time": True,
                "fundamentals_point_in_time_coverage_pct": 87.5,
                "status": "remediated (US) — point-in-time via SEC EDGAR",
            },
        })
        out = ve.get_track_record_summary("US", "medium")
        assert out[0]["fundamentals_point_in_time"] is True
        assert out[0]["fundamentals_point_in_time_coverage_pct"] == 87.5
        assert "remediated" in out[0]["dp026_status"]

    def test_in_run_forwards_not_remediated(self, monkeypatch):
        monkeypatch.setattr(ve, "get_latest_results", lambda horizon, universe: {
            "available": True, "beat_benchmark_pct": 40.0, "buy_hit_rate_pct": 45.0,
            "buy_signals": 100, "run_at": "2026-07-22T00:00:00Z",
            "data_limitations": {
                "fundamentals_point_in_time": False,
                "status": "disclosed, not remediated (India — no point-in-time source integrated)",
            },
        })
        out = ve.get_track_record_summary("IN", "medium")
        assert all(e["fundamentals_point_in_time"] is False for e in out)

    def test_legacy_run_without_data_limitations_reports_none_not_false(self, monkeypatch):
        # A run persisted before ANY DP-026 work must be distinguishable
        # from a genuinely-checked-and-contaminated one.
        monkeypatch.setattr(ve, "get_latest_results", lambda horizon, universe: {
            "available": True, "beat_benchmark_pct": 36.1, "buy_hit_rate_pct": 49.7,
            "buy_signals": 4551, "run_at": "2026-07-17T08:56:41Z",
            # no "data_limitations" key at all — genuinely legacy
        })
        out = ve.get_track_record_summary("IN", "short")
        assert out[0]["fundamentals_point_in_time"] is None
        assert out[0]["dp026_status"] is None

    def test_unavailable_result_still_omitted(self, monkeypatch):
        monkeypatch.setattr(ve, "get_latest_results", lambda horizon, universe: {"available": False})
        out = ve.get_track_record_summary("US", "long")
        assert out == []
