"""
Unit tests for sec_edgar_adapter.py's _facts_cache cap (Product Integrity
#020, corrected 2026-07-22).

#020 (2026-07-15/16) first bounded this cache — previously unbounded — by
reusing prediction_engine.py's _pred_cache cap value (300), reasoning it
was "not a new/untested number... already proven safe elsewhere in this
pipeline." That reasoning was itself the mistake, found during the
2026-07-22 US Daily Picks generation-reliability incident:
_pred_cache's entries are ~12 KB slim scored-result dicts (300 of those is
a few MB); this cache's entries are RAW SEC XBRL companyfacts payloads,
measured directly against live SEC EDGAR data at 4-8 MB of JSON text per
large-cap issuer (up to 17 years of tag history), ~15-25 MB once parsed
into Python objects (confirmed via tracemalloc — this single call site
accounted for 174 MiB / 2.8M retained objects across just 10 candidates in
a reproduction run). 300 entries of THIS cache's actual size is 4.5-7.5 GB
— the dominant, previously unidentified root cause of the memory aborts
recurring since 2026-07-15, including one that occurred AFTER #020's fix
shipped (2026-07-22, mid-way through the very first horizon, before the
cache even reached its old 300-entry cap). Re-capped at 25 — see
sec_edgar_adapter.py's own comment for the sizing rationale.

No live network calls — tests call the internal _facts_cache_set helper
directly and inspect the module-level cache dict.
"""
import services.sec_edgar_adapter as sea


def _reset_cache():
    sea._facts_cache.clear()


def test_facts_cache_max_is_sized_for_its_own_payload_not_borrowed():
    """2026-07-22: must NOT merely equal _pred_cache's cap again — that
    was the bug. Must be small enough that even a worst-case-sized cache
    (25 entries x ~25 MB measured worst case) stays a modest fraction of
    a real container's memory budget, not gigabytes."""
    from services.prediction_engine import _CACHE_MAX as _pred_cache_max
    assert sea._FACTS_CACHE_MAX <= 50, (
        "this cache's entries are measured at 15-25 MB each (full SEC "
        "XBRL companyfacts payloads) — a cap anywhere near _pred_cache's "
        "300 (12 KB entries) reintroduces the 2026-07-22 incident's root "
        "cause (multi-GB cache footprint)"
    )
    assert sea._FACTS_CACHE_MAX > 0
    # Explicitly must NOT equal the unrelated cache's cap — that equality
    # is exactly what masked the real, much-larger-per-entry sizing need.
    assert sea._FACTS_CACHE_MAX != _pred_cache_max


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
