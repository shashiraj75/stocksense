# Product Integrity Workstream #006 — Phase GPI-1: Explicit Market Routing for the Validation Engine

**Status:** Backend code fix implemented and unit-tested against mocked yfinance data only. **No validation run was performed.** This is a code-correctness fix, not a data-repair operation — see Section 4.

**Scope note:** this is a narrowly scoped backend change to `backend/services/validation_engine.py`'s per-stock Yahoo symbol routing. It does not touch prediction scoring, Daily Picks generation, outcome resolution, Phase 1A/1A.3, or any database schema. Nothing was staged, committed, or pushed as part of this change (per instruction, pending explicit approval).

## 1. Confirmed defect (D1, from Product Integrity #005's global audit)

`_backtest_stock()` used to determine each stock's Yahoo Finance ticker with a ticker-shape heuristic instead of the caller's own known market context:

```python
is_us   = not symbol.endswith(".NS") and "." not in symbol and len(symbol) <= 5
yf_sym  = symbol if is_us else symbol + ".NS"
```

`run_validation()` already knows the correct market unambiguously — it is selecting one of exactly three universes (`nifty100`, `midcap`, `us`) — but never passed that context down. The heuristic instead guessed from the symbol string's own shape (length ≤ 5, no dot, no `.NS` suffix), which misroutes any short, dot-free NSE symbol to a bare Yahoo ticker.

Product Integrity #005 confirmed this misclassified 47/134 (35%) Nifty-100 symbols and 19/112 (17%) mid-cap symbols, including a confirmed live case: `INFY` resolved to its NYSE ADR (USD-denominated) instead of its NSE line (INR-denominated) — a different instrument on a different exchange in a different currency, not merely a "wrong suffix."

## 2. Affected India validation universes

Both India universes run through `run_validation()` were exposed to this defect:

- **`nifty100`** (Nifty 100 large-cap India, ~134 configured symbols) — confirmed 47 misrouted symbols, including `INFY`, `TCS`, `SBIN`, `M&M`.
- **`midcap`** (NSE mid-cap sample, ~112 configured symbols) — confirmed 19 misrouted symbols.

The `us` universe was not affected by this specific defect (its symbols already routed correctly under the old heuristic), but its routing is now handled by the same explicit contract rather than surviving by heuristic coincidence.

## 3. The correction — explicit market contract

`_backtest_stock()` no longer infers market from a symbol's shape. It now receives `market` ("IN" | "US") explicitly from the caller:

```python
def _backtest_stock(
    symbol: str,
    horizon: str,
    benchmark_df: pd.DataFrame | None,
    market: str,
    *,
    universe: str | None = None,
) -> list[dict]:
```

Routing is delegated to a new pure helper, `_resolve_yahoo_symbol(symbol, market)`:

- `market == "IN"` → append `.NS` unless the symbol is already `.NS`-suffixed. Never falls back to a bare/US ticker.
- `market == "US"` → the symbol is used exactly as configured, including any dotted/dashed class-share form (e.g. `BRK.B`). Never suffixed.
- Any other `market` value → raises `ValueError` immediately, before any yfinance call. This is a fail-closed contract: an invalid or missing market never silently defaults to India or US.

`run_validation()` now maps its universe to a market explicitly via a module-level `UNIVERSE_MARKET = {"nifty100": "IN", "midcap": "IN", "us": "US"}` table (falling back to `"IN"` for an unrecognized universe string, mirroring the existing `universe_map.get(universe, NIFTY_100)` fallback rather than introducing a new failure mode) and passes the resolved `market` into every `_backtest_stock` task submitted to the executor. The previously dead `yf_suffix` local variable (computed but never referenced again) was removed.

**Naming cleanup** (within this file only, no behavior change): internal identifiers describing the benchmark series were renamed from `nifty_*` to `benchmark_*` throughout `_score_at`, `_backtest_stock`, and `_compute_metrics` (`nifty_close`→`benchmark_close`, `nifty_fwd_ret`→`benchmark_fwd_ret`, `nifty_df`→`benchmark_df`, `nifty_avg_ret`→`benchmark_avg_ret`, `_compute_metrics`'s `nifty_return_pct` parameter→`benchmark_return_pct`), since the benchmark is Nifty 50 for India universes but S&P 500 for the US universe. The **persisted** dict/column key `nifty_fwd_ret_pct` (used in `val_signals`, both Postgres and SQLite schemas) was deliberately left unchanged to avoid a schema migration — a comment at its assignment site documents this.

**Failure observability:** a symbol fetch failure or an insufficient-history early return now logs (`log.warning` / `log.info`) the original symbol, the resolved Yahoo symbol, the market, the universe, and the horizon — replacing a bare `print()` that carried none of that context.

## 4. What this change explicitly does NOT do

- **No validation rerun was performed.** No validation job was started, no `run_validation()` call was made against real market data providers, and no `/api/validation/*` endpoint was invoked. All test coverage below runs exclusively against mocked yfinance data.
- **All previously stored validation results (SQLite and Postgres, both `nifty100` and `midcap` universes) remain untrusted.** They were computed under the old defective routing and are not corrected retroactively by this code change — fixing the code path does not repair statistics that were already computed with the old, wrong routing.
- **The Phase GPI-0 frontend integrity hold (`INTEGRITY_HOLD_ACTIVE` in `frontend/src/components/ValidationIntegrityHold.tsx`, Product Integrity #005) must remain enabled.** This phase satisfies exactly one of GPI-0's five documented removal criteria (item 1: "`validation_engine.py`'s market-routing heuristic is fixed"). The other four — outcome entry-price/resolution-price reconciliation (D2), live population of `benchmark_return_5d/20d/60d` (D3), and market filtering on `/api/validation/results` and `/api/picks/performance` (D4, D5) — remain open.
- **A controlled rerun of validation (for `nifty100` and `midcap`) and independent verification of its output are separate, not-yet-started follow-up steps**, out of scope for this phase.

## 5. Effective sample-size finding

Inspection confirmed `metrics["n_stocks_tested"]` has always reported the size of the *requested* universe, not the count of symbols that actually returned usable signals (a symbol with insufficient history, or a fetch failure, silently contributes zero signals while still counting toward `n_stocks_tested`). This does not require a schema change to address — both fields live inside the existing JSON/JSONB `summary` column — so two new fields were added alongside the unchanged `n_stocks_tested`:

- `n_stocks_requested` — identical to `n_stocks_tested` today; explicit alias for clarity going forward.
- `n_stocks_with_signals` — count of symbols whose backtest actually produced at least one signal.

`n_stocks_tested` itself was left unchanged for backward compatibility with any existing consumer of that key.

## 6. Pre-commit hardening — unknown universe now fails closed

A final contract review, done before committing this phase, found one remaining silent-fallback gap that the initial implementation had carried over unchanged from the pre-existing code: `run_validation()` resolved an unrecognized `universe` string via `universe_map.get(universe, NIFTY_100)`, silently substituting the India large-cap list, and `market = UNIVERSE_MARKET.get(universe, "IN")` silently defaulted the market to `"IN"`. Neither of these was reachable through the API (`api/routers/validation.py`'s `/run` endpoint already constrains `universe` to `Literal["nifty100", "midcap", "us"]` via FastAPI, and `api/main.py`'s scheduler only ever iterates the literal tuple `("nifty100", "midcap", "us")`), but it was a latent contract gap in `run_validation()` itself.

This was closed by adding `_require_known_universe(universe)`, called as the first statement in `run_validation()` — before `_init_db()`, before the benchmark fetch, and before any executor submission. It raises `ValueError` for any universe string other than the exact, case-sensitive `"nifty100"`, `"midcap"`, or `"us"` — no trimming, no case normalization, no aliasing. `stocks = universe_map[universe]` and `market = UNIVERSE_MARKET[universe]` were changed from `.get(..., default)` to strict indexing, since the guard above now makes any other value structurally unreachable.

## 7. Test coverage

`backend/tests/unit/test_validation_engine_market_routing.py` (49 tests, all passing) — every yfinance call mocked, zero network access, zero database writes:

- `_resolve_yahoo_symbol` direct coverage: India bare-symbol suffixing, already-suffixed passthrough, US verbatim passthrough (including punctuated class-share tickers), invalid-market fail-closed (including case-sensitivity — `"in"` is not silently normalized to `"IN"`).
- `UNIVERSE_MARKET` wiring: `nifty100`/`midcap` → `IN`, `us` → `US`; an unrecognized universe key is proven to be structurally absent (`KeyError` on strict indexing, no `.get(..., default)` fallback anywhere in the production path).
- `_require_known_universe` / unknown-universe fail-closed coverage: unknown string, empty string, whitespace-padded, wrong-case, and `None` universes are all proven to raise `ValueError` *before* `_init_db`, `yf.Ticker`, or `_backtest_stock` are ever called (each is wired as a canary that raises `AssertionError` if reached first), and that the rejection leaves `_run_status["running"]` at `False` — no stuck state. All three valid universes are proven to pass the guard.
- Exhaustive iteration over the full configured `NIFTY_100`, `NSE_MIDCAP`, and `US_BASKET` lists, asserting every single symbol resolves per the new explicit contract.
- Regression proof reconstructing the *old* heuristic (not by calling removed production code) to confirm it would have misrouted a confirmed ≥20-symbol subset of Nifty-100 including `INFY`/`TCS`/`SBIN`, and that the new contract resolves every one of them correctly.
- `_backtest_stock` routing proof via a fake `yf.Ticker` that records every symbol it was constructed with, including proof that an invalid market raises before any yfinance call is made at all.
- Unchanged-behavior proof: entry/exit price and alpha arithmetic checked against a manually computed expectation on synthetic OHLCV data; benchmark reindex/alignment checked against a manual `reindex().ffill().bfill()`; a spy on `compute_indicators` confirming the look-ahead-bias guarantee (only `df.iloc[:i+1]` is ever visible at signal date `i`) is unchanged.
- `_compute_metrics`'s `benchmark_return_pct` rename confirmed to be a pure rename (output values unchanged).
- `run_validation` end-to-end wiring proof (`_backtest_stock`, yfinance, DB init, and the SQLite connection all mocked) confirming every symbol in each universe is submitted with the correct explicit market.
- Effective sample-size truthfulness: `n_stocks_requested` proven to equal the configured universe size; a symbol seeded with 5 signals plus a symbol seeded with 1 signal (6 total signal rows) is proven to count as `n_stocks_with_signals == 2`, not `6` — i.e. distinct symbols, not `len(all_signals)`; every symbol in a universe returning signals proves `n_stocks_with_signals == n_stocks_requested` (never exceeds it); a universe where nothing returns signals proves `n_stocks_with_signals == 0`.

Full backend suite: **2008 passed**, 0 failed, run with `DATABASE_URL`/`USE_POSTGRES` unset. No local backend server was started for any of this verification.

## 8. Explicit confirmations

- No validation job was run; no `run_validation()` call reached a real data provider.
- No external market-data fetch occurred anywhere in this phase's testing — every yfinance call was mocked.
- No validation result was written to Postgres or SQLite.
- No Daily Picks generation or backfill endpoint was called.
- No production database or `alpha_engine.db` was changed by this phase (`alpha_engine.db`'s pre-existing modified state, from before this phase began, is untouched — same file size, same content, verified via `git diff --stat`).
- No deployment occurred.
- Phase 1A / 1A.3 and outcome-resolution code were not touched.
- Nothing was staged, committed, or pushed.
