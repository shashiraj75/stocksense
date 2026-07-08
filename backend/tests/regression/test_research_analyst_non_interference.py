"""
Phase 1 spec T-10/T-11/T-12 + real-cache safety: structural proof that
the research_analyst package touches nothing and nothing touches it.

- T-10: AST-level allowlist — the package imports stdlib + itself only;
  no provider, engine, adapter, router, network, or DB import exists.
- T-12: static non-interference — no module under backend/services or
  backend/api imports research_analyst (mirrors the RCI Sprint #003
  static-import-absence pattern).
- T-11: the builder works with PredictionEngine.predict monkeypatched to
  raise and with socket creation blocked — it can never be the thing
  that computes or fetches.
- Cache safety: building from the REAL shared _pred_cache entry leaves
  the cached object identity and content byte-for-byte unchanged.
"""

import ast
import copy
import json
import socket
import time
from pathlib import Path

import pytest

from services.research_analyst.snapshot_builder import build_live_intelligence_snapshot
from tests.fixtures.research_analyst_fixtures import make_predict_result

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = BACKEND_ROOT / "services" / "research_analyst"

# The complete import surface Phase 1 permits itself (spec §11 rule 3).
_ALLOWED_MODULES = {
    "__future__", "copy", "dataclasses", "enum", "hashlib", "json",
    "numbers", "re", "types", "typing",
}
_ALLOWED_PREFIXES = ("services.research_analyst",)


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class TestNoProviderOrEngineImports:
    def test_package_imports_are_allowlisted_only(self):
        py_files = sorted(PACKAGE_DIR.glob("*.py"))
        assert len(py_files) == 4, f"unexpected package contents: {py_files}"
        for path in py_files:
            for module in _imports_of(path):
                allowed = (module in _ALLOWED_MODULES
                           or module.startswith(_ALLOWED_PREFIXES))
                assert allowed, (
                    f"{path.name} imports {module!r}, outside the Phase 1 "
                    f"allowlist — providers/engines/routers/IO are prohibited")

    def test_specific_prohibited_imports_absent(self):
        all_imports = set()
        for path in PACKAGE_DIR.glob("*.py"):
            all_imports |= _imports_of(path)
        for banned in ("yfinance", "requests", "httpx", "aiohttp", "socket",
                       "urllib", "psycopg2", "sqlalchemy", "fastapi",
                       "services.prediction_engine", "services.daily_picks",
                       "services.market_data", "services.screener_data",
                       "services.sec_edgar_adapter", "services.postgres_store"):
            assert not any(m == banned or m.startswith(banned + ".") for m in all_imports), banned


class TestNoProductionCallSite:
    def test_no_production_module_imports_research_analyst(self):
        scanned = 0
        for root in (BACKEND_ROOT / "services", BACKEND_ROOT / "api"):
            for path in root.rglob("*.py"):
                if PACKAGE_DIR in path.parents:
                    continue
                scanned += 1
                for module in _imports_of(path):
                    assert "research_analyst" not in module, (
                        f"{path.relative_to(BACKEND_ROOT)} imports {module!r} — "
                        f"Phase 1 permits no production call site")
        assert scanned > 50  # sanity: the walk actually covered the tree


class TestBuilderNeverComputesOrFetches:
    def test_builds_with_prediction_engine_disabled_and_sockets_blocked(self, monkeypatch):
        import services.prediction_engine as pe

        def _forbidden(*a, **k):
            raise AssertionError("research_analyst must never invoke PredictionEngine or the network")

        monkeypatch.setattr(pe.PredictionEngine, "predict", _forbidden)
        monkeypatch.setattr(socket, "socket", _forbidden)
        snap = build_live_intelligence_snapshot(
            make_predict_result(), symbol="RELIANCE", market="IN", horizon="medium")
        assert snap.availability.owner_statuses["PredictionEngine"] == "SUPPORTED"


class TestRealPredCacheSafety:
    def test_shared_cache_entry_unchanged_by_snapshot_build(self):
        from services.prediction_engine import _pred_cache

        key = "___research_analyst_test___:IN:medium"
        fixture = make_predict_result()
        try:
            _pred_cache[key] = (time.time(), fixture)
            before = copy.deepcopy(fixture)
            before_json = json.dumps(before, sort_keys=True, default=str)

            snap = build_live_intelligence_snapshot(
                _pred_cache[key][1], symbol="RELIANCE", market="IN", horizon="medium")

            cached = _pred_cache[key][1]
            assert cached is fixture  # same object — nothing swapped it
            assert cached == before
            assert json.dumps(cached, sort_keys=True, default=str) == before_json
            # And the snapshot shares no identity with the cache entry, so
            # future snapshot consumers can never corrupt it either.
            assert snap.engine_outputs["business_quality"] is not cached["business_quality"]
        finally:
            _pred_cache.pop(key, None)

    def test_daily_picks_style_direct_read_sees_identical_dict(self):
        """Simulates the Daily Picks access pattern (direct dict read of
        the shared cache) around a snapshot build."""
        from services.prediction_engine import _pred_cache

        key = "___research_analyst_dp___:US:long"
        fixture = make_predict_result(market="US", symbol="AAPL", horizon="long")
        try:
            _pred_cache[key] = (time.time(), fixture)
            reader_view = _pred_cache[key][1]  # a concurrent reader's reference
            build_live_intelligence_snapshot(
                _pred_cache[key][1], symbol="AAPL", market="US", horizon="long")
            assert reader_view is _pred_cache[key][1]
            assert reader_view["signal"] == "BUY"
            assert reader_view["trade_levels"]["stop_loss"] == 2755.0
        finally:
            _pred_cache.pop(key, None)
