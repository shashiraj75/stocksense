"""
feature/daily-picks-conviction-gated-publication — corrective follow-up
(finding 3, follow-up to commit 5a006498).

`_build_summary`'s confidence-tone sentence used to read "... (X% AI
confidence)". Per the conviction-gated publication policy's terminology,
this must now read "Model Conviction X/100" consistently with the rest of
the page — no "% AI confidence" wording anywhere active.
"""

import services.daily_picks as dp


def _result(confidence):
    return {
        "company_name": "TEST", "symbol": "TEST", "confidence": confidence,
        "current_price": 100.0, "target_price": 110.0,
        "technical": {"score": 65}, "fundamental_score": {"score": 60},
        "sentiment_score": {}, "market_regime": {}, "global_context": {},
        "quality_factors": {},
    }


class TestBuildSummaryConvictionWording:
    def test_high_confidence_uses_model_conviction_wording(self):
        summary = dp._build_summary(_result(90), "long")
        assert "Model Conviction 90/100" in summary
        assert "AI confidence" not in summary

    def test_moderate_confidence_uses_model_conviction_wording(self):
        summary = dp._build_summary(_result(55), "long")
        assert "Model Conviction 55/100" in summary
        assert "AI confidence" not in summary

    def test_low_confidence_uses_model_conviction_wording(self):
        summary = dp._build_summary(_result(30), "long")
        assert "Model Conviction 30/100" in summary
        assert "AI confidence" not in summary

    def test_no_percent_ai_confidence_wording_for_any_confidence_band(self):
        for conf in (0, 10, 25, 49, 50, 69, 70, 100):
            summary = dp._build_summary(_result(conf), "long")
            assert "% AI confidence" not in summary
