"""
Wave C — AST-based deterministic-offline call-site guard for
acquire_price_path_evidence(...).

fetch_bars_fn/fetch_splits_fn/fetch_dividends_fn are ordinary Python
default-parameter values bound once at function-definition time (see
services.postmortem.price_path_acquisition.acquire_price_path_evidence),
not a live module-attribute lookup performed on every call. A test call
that omits one of these keywords silently falls through to the
production default, which for fetch_dividends_fn reaches live
yfinance — exactly the defect found and fixed in
test_price_path_transaction_boundary_mock.py and
test_price_path_acquisition_boundary.py.

A prior guard used source.count(...) / regex counting, which can be
fooled by the keyword name appearing in a docstring, comment, or
explanatory string rather than an actual call site. This guard instead
parses every covered file with ast.parse and inspects the keyword
arguments of each genuine ast.Call node whose callee resolves to
acquire_price_path_evidence, reporting the exact file/line of any
deficient call.
"""
import ast
import inspect
from pathlib import Path

import pytest

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Every test file with a direct call to acquire_price_path_evidence(...)
# as of this guard's authoring — inventoried via:
#   grep -rl "acquire_price_path_evidence(" tests/unit tests/regression
_COVERED_FILES = (
    "tests/unit/test_session_boundary.py",
    "tests/unit/test_price_path_acquisition_boundary.py",
    "tests/unit/test_price_path_transaction_boundary_mock.py",
)

_REQUIRED_KEYWORDS = ("fetch_bars_fn", "fetch_splits_fn", "fetch_dividends_fn")

# Narrow, named exceptions only — a call whose explicit purpose is
# testing the provider wrapper itself, at the actual provider boundary,
# with no override needed because it never reaches a fetch_*_fn
# parameter. None exist today; add here with a written justification
# if one is ever introduced.
_EXEMPT_CALL_LINES: dict[str, set[int]] = {}


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _find_deficient_calls(path: Path) -> list[tuple[int, list[str]]]:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    deficient = []
    exempt_lines = _EXEMPT_CALL_LINES.get(str(path.relative_to(_BACKEND_ROOT)), set())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _callee_name(node) != "acquire_price_path_evidence":
            continue
        if node.lineno in exempt_lines:
            continue
        present = {kw.arg for kw in node.keywords if kw.arg is not None}
        missing = [k for k in _REQUIRED_KEYWORDS if k not in present]
        if missing:
            deficient.append((node.lineno, missing))
    return deficient


@pytest.mark.unit
class TestAcquisitionCallSitesInjectAllOfflineFetchers:
    @pytest.mark.parametrize("relative_path", _COVERED_FILES)
    def test_every_call_site_names_all_three_fetch_callbacks(self, relative_path):
        path = _BACKEND_ROOT / relative_path
        assert path.is_file(), f"expected covered test file to exist: {relative_path}"
        deficient = _find_deficient_calls(path)
        assert not deficient, (
            f"{relative_path} has acquire_price_path_evidence(...) call site(s) missing "
            f"required offline fetch overrides (may reach the live network): {deficient}"
        )

    def test_covered_file_list_is_not_stale(self):
        """If a new test file gains a direct acquire_price_path_evidence
        call, it must be added to _COVERED_FILES above — this guard
        only protects files it knows about."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rl", "--include=*.py", "acquire_price_path_evidence(", "tests/unit", "tests/regression"],
            cwd=_BACKEND_ROOT, capture_output=True, text=True, timeout=30,
        )
        found = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        found.discard(str(Path(__file__).relative_to(_BACKEND_ROOT)))
        expected = set(_COVERED_FILES)
        assert found == expected, (
            f"the set of test files calling acquire_price_path_evidence(...) has changed — "
            f"update _COVERED_FILES in this guard. found={found - expected} (new/untracked), "
            f"missing={expected - found} (no longer present)"
        )

    def test_required_keywords_match_the_real_function_signature(self):
        """Guards the guard: if acquire_price_path_evidence's parameter
        names ever change, this test fails loudly instead of the AST
        check silently checking for keywords that no longer exist."""
        from services.postmortem.price_path_acquisition import acquire_price_path_evidence
        params = inspect.signature(acquire_price_path_evidence).parameters
        for keyword in _REQUIRED_KEYWORDS:
            assert keyword in params, f"{keyword} is no longer a parameter of acquire_price_path_evidence"
