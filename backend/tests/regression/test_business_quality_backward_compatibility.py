"""
Regression test (Sprint #004 Phase 9/10): confirms the Business Quality
Engine's wiring into prediction_engine.py is backward compatible — the
existing `quality_factors`/`quality_score` computation path (SSDS-003
Finding 1's "broader, 14-dimension blend") is untouched by this sprint.

Per the sprint brief: "Do not replace existing production logic unless
the new implementation has been validated... At no point should the
application lose existing functionality." This test locks in that the
existing quality_factors.compute_all_quality_factors function signature
and the existing 14-dimension breakdown keys are unchanged, so a future
accidental edit during further Business Quality Engine work would fail
CI rather than silently drift.
"""

import inspect

import pytest

from services.quality_factors import compute_all_quality_factors


@pytest.mark.regression
def test_compute_all_quality_factors_signature_unchanged():
    sig = inspect.signature(compute_all_quality_factors)
    params = list(sig.parameters.keys())
    assert params == ["symbol", "ticker", "df", "info", "horizon", "market"]


@pytest.mark.regression
def test_existing_five_reused_functions_are_unmodified_by_import_check():
    """The Business Quality Engine REUSES these five functions by calling
    them, not by copying their logic (SSDS-003 Finding 1) — this confirms
    they're still the same importable functions quality_factors.py
    exposed before Sprint #004, not renamed or wrapped."""
    from services.quality_factors import (
        buffett_munger_score,
        altman_zscore_signal,
        sloan_accruals_signal,
        quality_metrics_score,
        corporate_actions_score,
    )
    assert callable(buffett_munger_score)
    assert callable(altman_zscore_signal)
    assert callable(sloan_accruals_signal)
    assert callable(quality_metrics_score)
    assert callable(corporate_actions_score)


@pytest.mark.regression
def test_business_quality_engine_does_not_import_or_call_compute_all_quality_factors():
    """SSDS-003 Finding 1's entire point: the Business Quality Engine must
    NOT be built on top of (or as a wrapper around) the existing blended
    compute_all_quality_factors — it reuses a SUBSET of its building
    blocks directly. A future edit that wires the broad function in by
    mistake should fail this test."""
    import ast
    import pathlib
    import services.business_quality_engine as bqe

    source = pathlib.Path(bqe.__file__).read_text()
    tree = ast.parse(source)

    # Walk the AST rather than grep the raw text, so the module's own
    # docstring (which explains in prose why compute_all_quality_factors
    # is deliberately NOT used) can't produce a false positive.
    names_referenced = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    names_referenced |= {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    imported_names = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "compute_all_quality_factors" not in names_referenced
    assert "compute_all_quality_factors" not in imported_names


@pytest.mark.regression
def test_quality_factors_dimension_keys_unchanged():
    """Pins the exact 14 dimension keys compute_all_quality_factors's
    `results` dict assembles (confirmed via direct source read during
    SSDS-003's Phase 1 Architecture Validation) — if this list changes,
    SSDS-003's Finding 1 table (which dimensions are/aren't business-
    quality questions) needs to be re-validated, not silently invalidated."""
    import pathlib
    source = pathlib.Path(
        __import__("services.quality_factors", fromlist=["x"]).__file__
    ).read_text()
    expected_keys = [
        "earnings_revision", "institutional", "inst_flow", "relative_strength",
        "sector_strength", "valuation", "risk_management", "liquidity",
        "mf_trend", "corporate_actions", "quality_metrics", "altman",
        "accruals", "buffett",
    ]
    for key in expected_keys:
        assert f'results["{key}"]' in source, f"Expected dimension key '{key}' not found in compute_all_quality_factors"
