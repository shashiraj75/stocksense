"""
Production outage hotfix — stock-universe generator dropped is_known_symbol.

Root cause: api/routers/predictions.py imports is_known_symbol from
services.stock_universe. The committed services/stock_universe.py has always
carried US_SYMBOLS/IN_SYMBOLS/is_known_symbol, but scripts/generate_stock_universe.py's
write_universe() template never included them — so the next automatic
startup refresh (services/stock_universe.py is regenerated from this
template, not hand-edited) silently overwrote the committed file without
that block, and the following process restart crashed with:

    ImportError: cannot import name 'is_known_symbol' from 'services.stock_universe'

This is the same class of bug TRACKING_ONLY_SYMBOLS was already fixed for
(see write_universe()'s own comment) — a hand-edit to the generated file
that the generator's template didn't know about, wiped on the next refresh.

This test drives write_universe() end-to-end against a temp file with tiny
fixtures and imports the result, so a regression here fails a test instead
of surfacing as a production ImportError on the next restart.
"""

import importlib.util
import os

import pytest

import scripts.generate_stock_universe as gsu

_TINY_US = [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation")]
_TINY_IN = [("RELIANCE", "Reliance Industries Limited"), ("TCS", "Tata Consultancy Services Limited")]
_TINY_CRYPTO = [("BTC", "Bitcoin"), ("ETH", "Ethereum")]


def _load_module_from_path(path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def generated_universe(tmp_path, monkeypatch):
    """Redirects OUT_FILE to a temp path, runs write_universe() with tiny
    fixtures, and returns the freshly-imported generated module."""
    out_file = tmp_path / "stock_universe_generated.py"
    monkeypatch.setattr(gsu, "OUT_FILE", str(out_file))

    gsu.write_universe(_TINY_US, _TINY_IN, _TINY_CRYPTO)

    assert out_file.exists(), "write_universe() did not produce OUT_FILE"
    return _load_module_from_path(str(out_file), "generated_stock_universe_under_test")


@pytest.mark.unit
class TestGeneratedUniverseHasIsKnownSymbol:
    def test_is_known_symbol_exists(self, generated_universe):
        assert hasattr(generated_universe, "is_known_symbol")
        assert callable(generated_universe.is_known_symbol)

    def test_known_us_symbol_returns_true(self, generated_universe):
        assert generated_universe.is_known_symbol("AAPL", "US") is True

    def test_known_in_symbol_returns_true(self, generated_universe):
        assert generated_universe.is_known_symbol("RELIANCE", "IN") is True

    def test_unknown_us_symbol_returns_false(self, generated_universe):
        assert generated_universe.is_known_symbol("NOTAREALTICKER", "US") is False

    def test_unknown_in_symbol_returns_false(self, generated_universe):
        assert generated_universe.is_known_symbol("NOTAREALTICKER", "IN") is False

    def test_non_us_in_markets_remain_fail_open(self, generated_universe):
        """CRYPTO (and anything else) has no static allow-list — must not
        be gated by this check, matching the committed function's contract."""
        assert generated_universe.is_known_symbol("SOMEUNKNOWNCOIN", "CRYPTO") is True
        assert generated_universe.is_known_symbol("ANYTHING", "SOMETHING_ELSE") is True

    def test_case_insensitive(self, generated_universe):
        assert generated_universe.is_known_symbol("aapl", "US") is True
        assert generated_universe.is_known_symbol("reliance", "IN") is True

    def test_symbol_sets_derived_from_the_same_fixture_lists(self, generated_universe):
        assert generated_universe.US_SYMBOLS == {"AAPL", "MSFT"}
        assert generated_universe.IN_SYMBOLS == {"RELIANCE", "TCS"}


@pytest.mark.unit
class TestGeneratedUniverseStillHasExistingRequiredMembers:
    """Guards against this fix regressing the members write_universe()
    already correctly preserved before this change."""

    def test_tracking_only_symbols_exists(self, generated_universe):
        assert hasattr(generated_universe, "TRACKING_ONLY_SYMBOLS")
        assert "GLD" in generated_universe.TRACKING_ONLY_SYMBOLS

    def test_search_universe_exists_and_works(self, generated_universe):
        assert hasattr(generated_universe, "search_universe")
        results = generated_universe.search_universe("apple", "US")
        assert any(r["symbol"] == "AAPL" for r in results)


@pytest.mark.unit
class TestAtomicWrite:
    def test_no_leftover_tmp_file_after_a_successful_write(self, tmp_path, monkeypatch):
        out_file = tmp_path / "stock_universe_generated.py"
        monkeypatch.setattr(gsu, "OUT_FILE", str(out_file))

        gsu.write_universe(_TINY_US, _TINY_IN, _TINY_CRYPTO)

        assert out_file.exists()
        assert not os.path.exists(str(out_file) + ".tmp")

    def test_write_uses_os_replace_for_atomic_swap(self, tmp_path, monkeypatch):
        """A partial write must never be observable at OUT_FILE — write to a
        temp path in the same directory, then os.replace() into place."""
        out_file = tmp_path / "stock_universe_generated.py"
        monkeypatch.setattr(gsu, "OUT_FILE", str(out_file))

        replace_calls = []
        real_replace = os.replace

        def _spy_replace(src, dst):
            # At the moment os.replace() is called, the temp file must be
            # fully written and sit in the same directory as OUT_FILE.
            assert os.path.dirname(src) == os.path.dirname(str(out_file))
            replace_calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(gsu.os, "replace", _spy_replace)
        gsu.write_universe(_TINY_US, _TINY_IN, _TINY_CRYPTO)

        assert len(replace_calls) == 1
        assert replace_calls[0][1] == str(out_file)
