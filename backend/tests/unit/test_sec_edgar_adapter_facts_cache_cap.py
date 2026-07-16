"""
Unit tests for sec_edgar_adapter.py's _facts_cache cap (Product Integrity
#020) — a production investigation traced a 2026-07-15/16 Railway OOM
(US Daily Picks job stalled ~5h, manually recovered) to this cache: it was
unbounded (no cap, no eviction) while every other cross-run cache in the
prediction pipeline (prediction_engine.py's _pred_cache/_regime_cache) is
capped at 300 entries specifically "to prevent OOM on free-tier 512MB
Render". With the US universe raised to 400 symbols (Sprint #014), this
cache could grow to ~400 uncapped full companyfacts payloads (each
spanning up to 17 years of XBRL history) per run.

No live network calls — tests call the internal _facts_cache_set helper
directly and inspect the module-level cache dict.
"""
import services.sec_edgar_adapter as sea


def _reset_cache():
    sea._facts_cache.clear()


def test_facts_cache_max_matches_prediction_engines_established_cap():
    """Not a new/untested number — reuses the exact cap value already
    proven safe elsewhere in this pipeline."""
    from services.prediction_engine import _CACHE_MAX
    assert sea._FACTS_CACHE_MAX == _CACHE_MAX == 300


def test_inserting_up_to_the_cap_keeps_every_entry():
    _reset_cache()
    for cik in range(sea._FACTS_CACHE_MAX):
        sea._facts_cache_set(cik, (float(cik), {"cik": cik}))
    assert len(sea._facts_cache) == sea._FACTS_CACHE_MAX
    assert 0 in sea._facts_cache
    assert sea._FACTS_CACHE_MAX - 1 in sea._facts_cache
    _reset_cache()


def test_exceeding_the_cap_evicts_the_oldest_entry_not_a_random_one():
    _reset_cache()
    for cik in range(sea._FACTS_CACHE_MAX):
        sea._facts_cache_set(cik, (float(cik), {"cik": cik}))
    # One more insert past the cap — the entry with the smallest timestamp
    # (cik=0, inserted first) must be evicted, not an arbitrary one.
    sea._facts_cache_set(sea._FACTS_CACHE_MAX, (float(sea._FACTS_CACHE_MAX), {"cik": sea._FACTS_CACHE_MAX}))
    assert len(sea._facts_cache) == sea._FACTS_CACHE_MAX
    assert 0 not in sea._facts_cache
    assert sea._FACTS_CACHE_MAX in sea._facts_cache
    assert 1 in sea._facts_cache  # second-oldest survives
    _reset_cache()


def test_cache_never_exceeds_the_cap_across_a_400_symbol_sized_run():
    """The exact scenario that triggered the OOM: ~400 distinct CIKs in
    one Daily Picks run. Cache size must stay bounded throughout, not
    just at the end."""
    _reset_cache()
    max_size_seen = 0
    for cik in range(400):
        sea._facts_cache_set(cik, (float(cik), {"cik": cik}))
        max_size_seen = max(max_size_seen, len(sea._facts_cache))
    assert max_size_seen <= sea._FACTS_CACHE_MAX
    assert len(sea._facts_cache) == sea._FACTS_CACHE_MAX
    _reset_cache()


def test_re_inserting_an_existing_key_does_not_evict_and_updates_value():
    _reset_cache()
    sea._facts_cache_set(1, (1.0, {"v": "old"}))
    sea._facts_cache_set(1, (2.0, {"v": "new"}))
    assert len(sea._facts_cache) == 1
    assert sea._facts_cache[1] == (2.0, {"v": "new"})
    _reset_cache()


def test_fetch_company_facts_writes_through_the_capped_setter(monkeypatch):
    """Regression guard against a future edit reverting fetch_company_facts
    back to a direct dict assignment that bypasses the cap."""
    _reset_cache()

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"facts": {"us-gaap": {}}}

    monkeypatch.setattr(sea, "_get_with_retry", lambda url: _FakeResponse())
    for cik in range(sea._FACTS_CACHE_MAX + 10):
        sea.fetch_company_facts(cik)
    assert len(sea._facts_cache) == sea._FACTS_CACHE_MAX
    _reset_cache()
