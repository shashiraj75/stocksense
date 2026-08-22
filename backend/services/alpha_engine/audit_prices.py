"""
The conviction-gate audit's IMMUTABLE RUN-LEVEL PRICE SNAPSHOT.

Why this module exists
----------------------
The first version of the audit resolved prices with one `yfinance` call PER
OBSERVATION. With ~15,000 observations that is ~15,000 provider requests for
~900 distinct tickers — roughly a 16x waste, guaranteed to be rate-limited,
and, worse, NOT REPRODUCIBLE: the two return measures each made their own
calls, so they could silently observe different prices for the same symbol and
date, and nothing about the price data was retained after the run.

This module replaces that with a single explicit step:

    fetch ONE panel, ONCE, per unique (market, symbol) over ONE date range,
    in controlled batches, with retries and rate-limit backoff, using
    EXPLICIT provider parameters — then freeze it.

Both return measures read from that same frozen panel. The snapshot is written
OUTSIDE the repository and checksummed, so any number reported by the audit can
be recomputed from exactly the prices that produced it.

Scaling contract (asserted by tests)
------------------------------------
The number of provider requests is a function of the number of UNIQUE SYMBOLS
and the batch size — never of the number of observations:

    requests == ceil(n_unique_symbols / batch_size)   (plus retries)

Provider parameters are pinned explicitly rather than left to library
defaults, because the defaults change between `yfinance` releases and would
silently change every historical number:

    auto_adjust=False   raw OHLC; the audit's entry is a real session OPEN and
                        its exit a real session CLOSE, not a split/dividend
                        back-adjusted synthetic series.
    actions=False       no dividend/split columns.
    repair=False        no provider-side price "repair" heuristics.
    threads=False       deterministic, ordered, rate-limit-friendly requests.
    interval="1d"       daily bars.

Missingness policy
------------------
Perfect arithmetic reconciliation (fetched == included + excluded) does NOT
rule out selection bias: rows can reconcile perfectly while the unresolvable
ones are systematically different. This module therefore measures unresolved
rates and exposes an ABORT THRESHOLD on both the overall level and the
DIFFERENTIAL between comparison groups. Exceeding either is a hard stop, not a
footnote.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import pathlib
import time

log = logging.getLogger(__name__)

TICKER_SUFFIX = {"IN": ".NS", "US": ""}


def provider_ticker(market: str, symbol: str) -> str:
    """
    The exact ticker string this audit asks the provider for.

    Exposed as a function so a coverage report can state the ticker it
    actually used, rather than a reader having to re-derive it and hope the
    derivation matches. `fetch_panel` builds its ticker the same way.
    """
    return f"{symbol}{TICKER_SUFFIX.get(market, '')}"

# Pinned provider parameters — see the module docstring.
PROVIDER = "yfinance"
PROVIDER_PARAMS = {
    "auto_adjust": False,
    "actions": False,
    "repair": False,
    "threads": False,
    "interval": "1d",
    "group_by": "ticker",
    "progress": False,
}

DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0

# --- Missingness abort thresholds -----------------------------------------
# Above these the audit MUST stop rather than report a possibly-selected
# sample. They are deliberately strict: the whole point of the re-audit is
# that a clean denominator is not the same as an unbiased one.
MAX_UNRESOLVED_RATE = 0.35
MAX_GROUP_DIFFERENTIAL_UNRESOLVED_RATE = 0.10


class MissingnessAbort(RuntimeError):
    """
    Raised when unresolved-price rates exceed a threshold.

    Deliberately fatal. A run that trips this has NOT produced a usable
    population estimate, regardless of how neatly its row counts reconcile.
    """


class PriceSnapshot:
    """
    A frozen (market, symbol, session_date) -> (open, close) panel.

    Immutable by convention: `fetch` populates it once, `freeze` closes it, and
    every later read goes through `get_open` / `get_close`. Both return
    measures share ONE instance, which is what guarantees they cannot disagree
    about a price.
    """

    def __init__(self) -> None:
        # (market, symbol) -> {iso_date: {"open": float, "close": float}}
        self._panel: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
        self._frozen = False
        self.meta: dict = {
            "provider": PROVIDER,
            "provider_params": dict(PROVIDER_PARAMS),
            "batches": [],
            "symbols_requested": 0,
            "symbols_returned": 0,
            "symbols_failed": [],
            "requests_made": 0,
            "retries_made": 0,
            "fetched_at_utc": None,
            "date_range": None,
        }

    # -- construction ------------------------------------------------------

    def put(self, market: str, symbol: str, day: str, open_: float, close: float) -> None:
        if self._frozen:
            raise RuntimeError("price snapshot is frozen — it may not be mutated")
        self._panel.setdefault((market, symbol), {})[day] = {
            "open": float(open_), "close": float(close)}

    def freeze(self) -> "PriceSnapshot":
        self._frozen = True
        return self

    @property
    def frozen(self) -> bool:
        return self._frozen

    # -- reads -------------------------------------------------------------

    def has_symbol(self, market: str, symbol: str) -> bool:
        return bool(self._panel.get((market, symbol)))

    def get_open(self, market: str, symbol: str, day) -> float | None:
        return self._get(market, symbol, day, "open")

    def get_close(self, market: str, symbol: str, day) -> float | None:
        return self._get(market, symbol, day, "close")

    def first_session_on_or_after(self, market: str, symbol: str, day) -> str | None:
        """Earliest stored session date >= `day` for this symbol, or None."""
        series = self._panel.get((market, symbol))
        if not series:
            return None
        target = _iso(day)
        candidates = [d for d in series if d >= target]
        return min(candidates) if candidates else None

    def sessions(self, market: str, symbol: str) -> list[str]:
        return sorted(self._panel.get((market, symbol), {}))

    def _get(self, market, symbol, day, field) -> float | None:
        rec = self._panel.get((market, symbol), {}).get(_iso(day))
        if rec is None:
            return None
        v = rec.get(field)
        return v if _finite(v) else None

    # -- persistence -------------------------------------------------------

    def to_records(self) -> list[dict]:
        out = []
        for (market, symbol), series in sorted(self._panel.items()):
            for day in sorted(series):
                out.append({"market": market, "symbol": symbol, "date": day,
                            "open": series[day]["open"], "close": series[day]["close"]})
        return out

    def save(self, path) -> dict:
        """
        Write the snapshot outside the repository and return its checksum.

        Records are emitted in a fully deterministic order so the checksum is
        stable across runs of identical data.
        """
        p = pathlib.Path(path).expanduser().resolve()
        _refuse_inside_repo(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        records = self.to_records()
        payload = json.dumps(
            {"meta": self.meta, "records": records},
            indent=None, sort_keys=True, separators=(",", ":"))
        p.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return {"path": str(p), "sha256": digest, "n_records": len(records),
                "n_symbols": len(self._panel), "bytes": len(payload)}

    @classmethod
    def load(cls, path) -> "PriceSnapshot":
        p = pathlib.Path(path).expanduser().resolve()
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        snap = cls()
        snap.meta = data.get("meta", {})
        for rec in data.get("records", []):
            snap.put(rec["market"], rec["symbol"], rec["date"],
                     rec["open"], rec["close"])
        snap.meta["loaded_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return snap.freeze()


def fetch_panel(
    market_symbols: dict[str, list[str]],
    start: _dt.date,
    end: _dt.date,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    downloader=None,
    sleep=time.sleep,
) -> PriceSnapshot:
    """
    Fetch ONE price panel covering every unique symbol, in controlled batches.

    `market_symbols` maps market -> unique symbol list. Requests scale with
    UNIQUE SYMBOLS, never with observation count.

    `downloader` is injectable purely so tests can assert the batching and
    retry behaviour without touching the network; production passes None and
    the real `yfinance.download` is used with the pinned PROVIDER_PARAMS.

    On a batch failure the batch is retried up to `max_retries` times with
    linear backoff (rate limiting is the common cause, and backing off is the
    correct response). A batch that still fails has every symbol in it
    recorded in `meta["symbols_failed"]` — never silently dropped.
    """
    snap = PriceSnapshot()
    snap.meta["fetched_at_utc"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    snap.meta["date_range"] = [start.isoformat(), end.isoformat()]
    dl = downloader if downloader is not None else _yfinance_download

    for market, symbols in sorted(market_symbols.items()):
        uniq = sorted(set(symbols))
        snap.meta["symbols_requested"] += len(uniq)
        suffix = TICKER_SUFFIX.get(market, "")
        for i in range(0, len(uniq), batch_size):
            batch = uniq[i:i + batch_size]
            tickers = [provider_ticker(market, s) for s in batch]
            attempt = 0
            rows = None
            while attempt <= max_retries:
                snap.meta["requests_made"] += 1
                try:
                    rows = dl(tickers, start, end)
                    break
                except Exception as exc:  # noqa: BLE001 — retry then record
                    attempt += 1
                    snap.meta["retries_made"] += 1
                    log.warning(
                        "[audit_prices] batch %s..%s attempt %d failed: %s",
                        batch[0], batch[-1], attempt, exc)
                    if attempt > max_retries:
                        rows = None
                        break
                    sleep(backoff_seconds * attempt)
            snap.meta["batches"].append(
                {"market": market, "size": len(batch), "attempts": attempt + 1,
                 "ok": rows is not None})
            if rows is None:
                snap.meta["symbols_failed"].extend(f"{market}:{s}" for s in batch)
                continue
            for tkr, day, open_, close in rows:
                sym = tkr[:-len(suffix)] if suffix and tkr.endswith(suffix) else tkr
                if _finite(open_) and _finite(close):
                    snap.put(market, sym, _iso(day), open_, close)
        for s in uniq:
            if snap.has_symbol(market, s):
                snap.meta["symbols_returned"] += 1
            elif f"{market}:{s}" not in snap.meta["symbols_failed"]:
                snap.meta["symbols_failed"].append(f"{market}:{s}")
    return snap.freeze()


def _yfinance_download(tickers: list[str], start: _dt.date, end: _dt.date):
    """Real provider call. Yields (ticker, date, open, close) tuples."""
    import yfinance as yf

    data = yf.download(
        tickers,
        start=start.isoformat(),
        # yfinance's `end` is exclusive; extend by a day so `end` is included.
        end=(end + _dt.timedelta(days=1)).isoformat(),
        **PROVIDER_PARAMS,
    )
    if data is None or data.empty:
        return []
    out = []
    multi = hasattr(data.columns, "levels") and data.columns.nlevels > 1
    for tkr in tickers:
        try:
            sub = data[tkr] if multi else data
        except KeyError:
            continue
        if "Open" not in sub or "Close" not in sub:
            continue
        for ts, row in sub.iterrows():
            o, c = row.get("Open"), row.get("Close")
            if _finite(o) and _finite(c):
                out.append((tkr, ts.date(), float(o), float(c)))
    return out


def missingness_report(
    rows: list[dict],
    *,
    resolved_key: str,
    market_key: str = "market",
    horizon_key: str = "horizon",
    date_key: str = "reference_session_date",
    group_key: str = "comparison_group",
) -> dict:
    """
    Unresolved-price rates broken out by market, horizon, date and comparison
    group — the breakdown needed to see SELECTION, which a reconciling row
    count cannot show.

    `resolved_key` names a boolean field: True when the row's price resolved.
    """
    def tally(key):
        acc: dict[str, dict[str, int]] = {}
        for r in rows:
            k = str(r.get(key))
            slot = acc.setdefault(k, {"n": 0, "resolved": 0})
            slot["n"] += 1
            slot["resolved"] += bool(r.get(resolved_key))
        for slot in acc.values():
            slot["unresolved"] = slot["n"] - slot["resolved"]
            slot["unresolved_rate"] = slot["unresolved"] / slot["n"] if slot["n"] else None
        return acc

    total = len(rows)
    resolved = sum(1 for r in rows if r.get(resolved_key))
    by_group = tally(group_key)
    rates = [v["unresolved_rate"] for v in by_group.values()
             if v["unresolved_rate"] is not None and v["n"] >= 10]
    return {
        "n": total,
        "resolved": resolved,
        "unresolved": total - resolved,
        "unresolved_rate": (total - resolved) / total if total else None,
        "by_market": tally(market_key),
        "by_horizon": tally(horizon_key),
        "by_date": tally(date_key),
        "by_comparison_group": by_group,
        "max_group_differential": (max(rates) - min(rates)) if len(rates) >= 2 else None,
        "thresholds": {
            "max_unresolved_rate": MAX_UNRESOLVED_RATE,
            "max_group_differential_unresolved_rate":
                MAX_GROUP_DIFFERENTIAL_UNRESOLVED_RATE,
        },
    }


def enforce_missingness(report: dict, *, raise_on_breach: bool = True) -> dict:
    """
    Apply the abort thresholds to a `missingness_report`.

    Two independent guards:
      * LEVEL      — too much of the population is unresolvable to call the
                     result a population estimate at all.
      * DIFFERENTIAL — the comparison groups are unresolvable at materially
                     different rates, which is direct evidence of selection
                     between the very groups being compared. This one matters
                     even when the overall level looks acceptable.
    """
    breaches = []
    rate = report.get("unresolved_rate")
    if rate is not None and rate > MAX_UNRESOLVED_RATE:
        breaches.append(
            f"overall unresolved rate {rate:.3f} exceeds {MAX_UNRESOLVED_RATE}")
    diff = report.get("max_group_differential")
    if diff is not None and diff > MAX_GROUP_DIFFERENTIAL_UNRESOLVED_RATE:
        breaches.append(
            f"between-group unresolved differential {diff:.3f} exceeds "
            f"{MAX_GROUP_DIFFERENTIAL_UNRESOLVED_RATE} — the comparison groups "
            f"are not missing at the same rate, which is selection, not noise")
    report["breaches"] = breaches
    report["passed"] = not breaches
    if breaches and raise_on_breach:
        raise MissingnessAbort(
            "price missingness guard tripped: " + "; ".join(breaches))
    return report


# --- helpers --------------------------------------------------------------

def _iso(day) -> str:
    if isinstance(day, _dt.datetime):
        return day.date().isoformat()
    if isinstance(day, _dt.date):
        return day.isoformat()
    return str(day)[:10]


def _finite(x) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _refuse_inside_repo(path: pathlib.Path) -> None:
    repo_root = pathlib.Path(__file__).resolve().parents[3]
    if path == repo_root or repo_root in path.parents:
        raise ValueError(
            f"refusing to write the price snapshot inside the repository "
            f"({path}); row-level market data must never be committed to git")
