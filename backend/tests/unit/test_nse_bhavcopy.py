"""
Product Integrity #012 — tests for services.nse_bhavcopy, the NSE bhavcopy
last-resort price correction. All HTTP access is mocked; no real network
calls are made.
"""
import datetime

import pytest

import services.nse_bhavcopy as bhavcopy


SAMPLE_CSV = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
    "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
    "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
    "1018GS2026, GS, 15-Jul-2026, 104.12, 104.15, 104.15, 104.15, 104.15, 104.15, 104.15, 15, 0.02, 3, 15, 100.00\n"
    "LUPIN, EQ, 15-Jul-2026, 2469.20, 2480.00, 2509.90, 2477.50, 2493.00, 2493.00, 2495.60, 821069, 20490.63, 52372, 549751, 66.96\n"
    "20MICRONS, EQ, 15-Jul-2026, 205.30, 205.00, 207.79, 202.96, 204.11, 204.43, 204.93, 144897, 296.93, 2877, 69760, 48.14\n"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    with bhavcopy._lock:
        bhavcopy._cache.clear()
    yield
    with bhavcopy._lock:
        bhavcopy._cache.clear()


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_parses_close_price_for_eq_series_symbol():
    closes = bhavcopy._parse_csv(SAMPLE_CSV)
    assert closes["LUPIN"] == 2493.00
    assert closes["20MICRONS"] == 204.43


def test_excludes_non_eq_series_rows():
    closes = bhavcopy._parse_csv(SAMPLE_CSV)
    assert "1018GS2026" not in closes


def test_get_bhavcopy_close_returns_price_on_success(monkeypatch):
    monkeypatch.setattr(bhavcopy, "_fetch_and_parse", lambda d: {"LUPIN": 2493.00})
    result = bhavcopy.get_bhavcopy_close("lupin", datetime.date(2026, 7, 15))
    assert result == 2493.00


def test_get_bhavcopy_close_is_case_insensitive_on_symbol(monkeypatch):
    monkeypatch.setattr(bhavcopy, "_fetch_and_parse", lambda d: {"LUPIN": 2493.00})
    assert bhavcopy.get_bhavcopy_close("Lupin", datetime.date(2026, 7, 15)) == 2493.00
    assert bhavcopy.get_bhavcopy_close("LUPIN", datetime.date(2026, 7, 15)) == 2493.00


def test_get_bhavcopy_close_returns_none_for_unknown_symbol(monkeypatch):
    monkeypatch.setattr(bhavcopy, "_fetch_and_parse", lambda d: {"LUPIN": 2493.00})
    assert bhavcopy.get_bhavcopy_close("NOTASYMBOL", datetime.date(2026, 7, 15)) is None


def test_get_bhavcopy_close_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr(bhavcopy, "_fetch_and_parse", lambda d: None)
    assert bhavcopy.get_bhavcopy_close("LUPIN", datetime.date(2026, 7, 15)) is None


def test_fetch_is_only_called_once_per_date_within_ttl(monkeypatch):
    """A Daily Picks run checks ~400 symbols against the same session date —
    only the first lookup should trigger a real fetch."""
    call_count = {"n": 0}

    def _fake_fetch(d):
        call_count["n"] += 1
        return {"LUPIN": 2493.00}

    monkeypatch.setattr(bhavcopy, "_fetch_and_parse", _fake_fetch)
    for _ in range(5):
        bhavcopy.get_bhavcopy_close("LUPIN", datetime.date(2026, 7, 15))
    assert call_count["n"] == 1


def test_404_response_returns_none_without_retry(monkeypatch):
    calls = {"n": 0}

    def _fake_get(url, timeout):
        calls["n"] += 1
        return _FakeResponse(404)

    monkeypatch.setattr(bhavcopy._SESSION, "get", _fake_get)
    result = bhavcopy._fetch_and_parse(datetime.date(2026, 7, 15))
    assert result is None
    assert calls["n"] == 1  # 404 is not retried — not a transient failure


def test_network_exception_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def _fake_get(url, timeout):
        calls["n"] += 1
        raise Exception("connection reset")

    monkeypatch.setattr(bhavcopy._SESSION, "get", _fake_get)
    monkeypatch.setattr(bhavcopy.time, "sleep", lambda s: None)
    result = bhavcopy._fetch_and_parse(datetime.date(2026, 7, 15))
    assert result is None
    assert calls["n"] == bhavcopy._MAX_ATTEMPTS


def test_successful_fetch_parses_real_response_shape(monkeypatch):
    def _fake_get(url, timeout):
        return _FakeResponse(200, SAMPLE_CSV)

    monkeypatch.setattr(bhavcopy._SESSION, "get", _fake_get)
    result = bhavcopy._fetch_and_parse(datetime.date(2026, 7, 15))
    assert result["LUPIN"] == 2493.00


def test_url_uses_ddmmyyyy_format(monkeypatch):
    captured = {}

    def _fake_get(url, timeout):
        captured["url"] = url
        return _FakeResponse(200, SAMPLE_CSV)

    monkeypatch.setattr(bhavcopy._SESSION, "get", _fake_get)
    bhavcopy._fetch_and_parse(datetime.date(2026, 7, 15))
    assert "sec_bhavdata_full_15072026.csv" in captured["url"]
