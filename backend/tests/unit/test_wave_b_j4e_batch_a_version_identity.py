"""
Wave B, Stage J4E — Batch A red tests: version identity and
no-dual-semantic-authority (Gate 1, IDs WB-J4E-01..06).

Written and run against genuine PRE-FIX production code (HEAD
c91b7d8e6bdb694c069f716ab04777b5700336c4) — every test in this file is
expected to FAIL until the frozen-contract implementation lands. None of
these tests import a not-yet-existing symbol directly at module
collection time (that would be a collection error, not a red test) —
absent modules/symbols are probed with importlib.util.find_spec / getattr
so collection always succeeds and the assertion itself carries the
intended failure.
"""
import importlib
import importlib.util
import inspect

import pytest


def _find_module(dotted_name: str):
    spec = importlib.util.find_spec(dotted_name)
    return spec


# WB-J4E-01 — a governed 1.2.0 report identity module must exist.
def test_wb_j4e_01_governed_report_identity_module_exists():
    spec = _find_module("services.postmortem.current_report_generation")
    assert spec is not None, (
        "WB-J4E-01: services.postmortem.current_report_generation does not exist yet "
        "(expected red pre-fix: the current-version 1.2.0 orchestrator module is missing)."
    )


# WB-J4E-02 — price_path_identity must expose the 1.2.0 report schema version constant.
def test_wb_j4e_02_report_schema_version_1_2_0_constant_exists():
    from services.postmortem import price_path_identity
    assert getattr(price_path_identity, "GOVERNED_PRICE_PATH_REPORT_SCHEMA_VERSION", None) == "1.2.0", (
        "WB-J4E-02: price_path_identity.GOVERNED_PRICE_PATH_REPORT_SCHEMA_VERSION == '1.2.0' "
        "is not yet defined (expected red pre-fix)."
    )


# WB-J4E-03 — the governed price-path rules version constant must exist and equal 2.0.0.
def test_wb_j4e_03_governed_price_path_rules_version_constant_exists():
    from services.postmortem import price_path_identity
    assert getattr(price_path_identity, "GOVERNED_PRICE_PATH_RULES_VERSION", None) == "2.0.0", (
        "WB-J4E-03: price_path_identity.GOVERNED_PRICE_PATH_RULES_VERSION == '2.0.0' "
        "is not yet defined (expected red pre-fix)."
    )


# WB-J4E-04 — the governed claim rules version constant must exist and equal 2.0.0.
def test_wb_j4e_04_governed_claim_rules_version_constant_exists():
    from services.postmortem import price_path_identity
    assert getattr(price_path_identity, "GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION", None) == "2.0.0", (
        "WB-J4E-04: price_path_identity.GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION == '2.0.0' "
        "is not yet defined (expected red pre-fix)."
    )


# WB-J4E-05 — governed_calculation_version() must produce the exact contract format.
def test_wb_j4e_05_governed_calculation_version_format():
    from services.postmortem import price_path_identity
    fn = getattr(price_path_identity, "governed_calculation_version", None)
    assert fn is not None, (
        "WB-J4E-05: price_path_identity.governed_calculation_version is not yet defined "
        "(expected red pre-fix)."
    )
    result = fn(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0")
    assert result == "1.1.0+price_path:1.0.0+governed:2.0.0+src:1.1.0", (
        f"WB-J4E-05: governed_calculation_version produced {result!r}, expected the exact "
        "'<base>+price_path:<numerical>+governed:<governed>+src:<source>' contract format."
    )


# WB-J4E-06 — no dual semantic authority: a 1.2.0 report builder must exist and must NOT
# call the legacy detect_touches/classify_touch_order/build_touch_order_claim path.
def test_wb_j4e_06_governed_claims_module_does_not_import_legacy_touch_authority():
    spec = _find_module("services.postmortem.governed_price_path_claims")
    assert spec is not None, (
        "WB-J4E-06: services.postmortem.governed_price_path_claims does not exist yet "
        "(expected red pre-fix: the governed-only claim builder module is missing)."
    )
    import ast
    module = importlib.import_module("services.postmortem.governed_price_path_claims")
    tree = ast.parse(inspect.getsource(module))
    imported_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_names = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    referenced = imported_names | called_names
    for forbidden in ("detect_touches", "classify_touch_order", "build_touch_order_claim"):
        assert forbidden not in referenced, (
            f"WB-J4E-06: governed_price_path_claims.py references legacy symbol {forbidden!r} — "
            "no dual semantic authority is permitted in a 1.2.0 claim builder."
        )
