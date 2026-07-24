"""
2026-07-23/24 US Daily Picks memory incident — SEC companyfacts reuse.

The 2026-07-22 horizon-major Phase 1 ordering re-visited each symbol ~400
tasks after its companyfacts payload had been evicted from the 25-entry
_facts_cache, so every symbol's 4-8 MB SEC JSON was downloaded and parsed
~three times per run (~1,200 fetch/parse cycles instead of ~400) — the
dominant allocation-churn driver behind the fragmentation RSS ratchet that
aborted the July 23 and July 24 runs.

The chunked candidate-major ordering guarantees each symbol's three
horizons run consecutively within a chunk no larger than the cache
capacity, so under normal successful-cache behavior companyfacts loads at
most ONCE per unique symbol per generation run.

These tests prove that property end-to-end through the REAL
sec_edgar_adapter cache (only the network layer `_get_with_retry` is
monkeypatched), driven by the REAL _generate_picks_inner chunked loop with
a _predict_stock stub that performs the same per-(symbol, horizon) facts
lookup the production engine performs (prediction_engine ->
compute_us_financial_strength -> fetch_us_fundamentals_sec_edgar ->
fetch_company_facts).
"""
import json
from unittest.mock import MagicMock, patch

_FAKE_REGIME = {"regime_id": 0, "label": "neutral", "description": "", "weight_multipliers": {}}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"facts": {"us-gaap": {}}}

    def json(self):
        return self._payload


def _cik_for(sym: str) -> int:
    return 100000 + int(sym.replace("SYM", ""))


def _run_generation_with_real_facts_cache(candidates, network_mock,
                                          failing_ciks=frozenset()):
    """Run _generate_picks_inner with a _predict_stock stub that performs
    one real fetch_company_facts() per call — the same lookup cardinality
    as the production engine — against the real 25-entry TTL cache."""
    import services.daily_picks as dp
    from services import sec_edgar_adapter as sea

    with sea._facts_lock:
        sea._facts_cache.clear()

    def fake_predict(sym, horizon, market):
        facts = sea.fetch_company_facts(_cik_for(sym))
        # Negative-cache contract: a failed CIK returns None, a good one a dict.
        if _cik_for(sym) in failing_ciks:
            assert facts is None
        else:
            assert facts is not None
        return None  # pick construction is irrelevant to this proof

    with patch.object(sea, "_get_with_retry", side_effect=network_mock), \
         patch.object(sea, "_throttle"), \
         patch("services.daily_picks._bulk_screen",
               return_value=(candidates, 10, "screener", False, 20,
                             {"universe_candidate_count": len(candidates), "attempts": 1,
                              "reason": "healthy_screener_universe", "error_category": "none",
                              "tier_map": {}})), \
         patch("services.daily_picks._predict_stock", side_effect=fake_predict), \
         patch("services.daily_picks._write_score_snapshots"), \
         patch("services.daily_picks.yf", MagicMock()), \
         patch("services.alpha_engine.regime_cluster.detect_regime", return_value=_FAKE_REGIME), \
         patch("services.alpha_engine.ic_engine.get_production_ic_weights", return_value={}), \
         patch("services.alpha_engine.store.log_prediction"), \
         patch("services.global_context.get_global_context", return_value={}), \
         patch("os.getenv", return_value=None), \
         patch("builtins.open", MagicMock()), \
         patch("json.dump"):
        # memory_guard's run-start release would legitimately clear the
        # facts cache once before Phase 1 — irrelevant here (cache is
        # cleared above anyway), but pin it to a no-op so this test only
        # measures the Phase 1 ordering property.
        with patch("services.memory_guard.clear_safe_caches", return_value=[]):
            dp._generate_picks_inner("US", job_id=None)

    with sea._facts_lock:
        sea._facts_cache.clear()


def test_companyfacts_loaded_exactly_once_per_unique_symbol_per_run():
    """60 candidates (> 2 chunks of 25) x 3 horizons = 180 facts lookups —
    but exactly 60 network loads: one per unique symbol, horizons 2 and 3
    served from the warm cache, including for symbols at every chunk
    boundary."""
    candidates = [f"SYM{i:02d}" for i in range(60)]
    network_calls: list[str] = []

    def network_mock(url):
        network_calls.append(url)
        return _FakeResponse()

    _run_generation_with_real_facts_cache(candidates, network_mock)

    assert len(network_calls) == len(candidates), (
        f"expected exactly one companyfacts network load per unique symbol "
        f"({len(candidates)}), got {len(network_calls)} — horizons 2/3 must "
        f"reuse the warm cache"
    )
    # Per-CIK: each URL fetched exactly once — no symbol double-fetched at
    # a chunk boundary, none skipped.
    assert len(set(network_calls)) == len(candidates)


def test_chunk_boundary_symbols_do_not_refetch():
    """Symbols at indices 24/25/26 straddle the 25-entry chunk boundary —
    each must still load exactly once."""
    candidates = [f"SYM{i:02d}" for i in range(30)]
    per_url_counts: dict[str, int] = {}

    def network_mock(url):
        per_url_counts[url] = per_url_counts.get(url, 0) + 1
        return _FakeResponse()

    _run_generation_with_real_facts_cache(candidates, network_mock)

    boundary_ciks = {_cik_for(f"SYM{i:02d}") for i in (24, 25, 26)}
    for url, count in per_url_counts.items():
        assert count == 1, f"{url} fetched {count} times — chunk boundary caused a refetch"
    # And the boundary symbols were genuinely exercised:
    fetched_ciks = {int(u.rsplit("CIK", 1)[1].split(".")[0]) for u in per_url_counts}
    assert boundary_ciks <= fetched_ciks


def test_failed_fetch_negative_cache_still_single_load_and_correctly_handled():
    """A CIK whose fetch fails is negative-cached (None) — its horizons 2/3
    must reuse that None without a second network attempt, and healthy
    symbols are unaffected."""
    candidates = [f"SYM{i:02d}" for i in range(10)]
    failing = {_cik_for("SYM03")}
    network_calls: list[str] = []

    def network_mock(url):
        network_calls.append(url)
        cik = int(url.rsplit("CIK", 1)[1].split(".")[0])
        if cik in failing:
            return _FakeResponse(status_code=500, payload={})
        return _FakeResponse()

    _run_generation_with_real_facts_cache(candidates, network_mock, failing_ciks=failing)

    # Still exactly one network attempt per symbol — the failure is cached
    # as None for the rest of the run, not retried per horizon.
    assert len(network_calls) == len(candidates)
    assert len(set(network_calls)) == len(candidates)
