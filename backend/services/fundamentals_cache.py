"""
Cached fundamentals for the full NSE universe, refreshed nightly by
fundamentals_refresh.py. Backs the Multibagger Screen feature — screening
2,300+ stocks live against screener.in on every request isn't viable (too
slow, and it would hammer screener.in hard enough to risk getting our IP
blocked, which we also depend on for predictions and the Fundamentals tab).
Instead we scrape once overnight and run instant SQL filters against this
table.
"""
import os
from datetime import datetime, timezone


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def ensure_table():
    with _conn() as conn:
        # Fresh installs get the full schema directly.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_fundamentals_cache (
                symbol                   TEXT NOT NULL,
                market                   TEXT NOT NULL DEFAULT 'IN',
                company_name             TEXT,
                sector_name              TEXT,
                industry_name            TEXT,
                is_financial             BOOLEAN NOT NULL DEFAULT FALSE,
                market_cap_cr            NUMERIC,
                market_cap_usd_m         NUMERIC,
                pe_ratio                 NUMERIC,
                roe_pct                  NUMERIC,
                roe_5y_pct               NUMERIC,
                roce_pct                 NUMERIC,
                debt_to_equity_pct       NUMERIC,
                promoter_holding_pct     NUMERIC,
                promoter_pledge_pct      NUMERIC,
                insider_holding_pct      NUMERIC,
                sales_growth_3y_pct      NUMERIC,
                sales_growth_5y_pct      NUMERIC,
                profit_growth_3y_pct     NUMERIC,
                profit_growth_5y_pct     NUMERIC,
                opm_pct                  NUMERIC,
                interest_coverage_ratio  NUMERIC,
                ev_ebitda                NUMERIC,
                price_to_sales           NUMERIC,
                operating_cf_latest_cr   NUMERIC,
                updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (symbol, market)
            )
        """)

        # Migration path for a table created before the `market` column
        # existed (PRIMARY KEY was just `symbol`). Guarded by a cheap
        # information_schema check so this only runs once ever, not on
        # every call — ensure_table() runs on every screen/status request.
        has_market_col = conn.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'stock_fundamentals_cache' AND column_name = 'market'
        """).fetchone()
        if not has_market_col:
            conn.execute("ALTER TABLE stock_fundamentals_cache ADD COLUMN market TEXT NOT NULL DEFAULT 'IN'")
            conn.execute("ALTER TABLE stock_fundamentals_cache ADD COLUMN IF NOT EXISTS market_cap_usd_m NUMERIC")
            conn.execute("ALTER TABLE stock_fundamentals_cache ADD COLUMN IF NOT EXISTS insider_holding_pct NUMERIC")
            # Existing rows predate `market` and were always IN-only data,
            # so the DEFAULT 'IN' above already backfills them correctly.
            conn.execute("ALTER TABLE stock_fundamentals_cache DROP CONSTRAINT IF EXISTS stock_fundamentals_cache_pkey")
            conn.execute("ALTER TABLE stock_fundamentals_cache ADD PRIMARY KEY (symbol, market)")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamentals_cache_updated ON stock_fundamentals_cache(updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamentals_cache_market ON stock_fundamentals_cache(market)")

        # No PII here, but closing the Supabase "RLS disabled" finding for
        # every public table, not just the ones with sensitive columns —
        # this connects as `postgres`, which has BYPASSRLS by default, so
        # our own access is unaffected. Idempotent.
        conn.execute("ALTER TABLE stock_fundamentals_cache ENABLE ROW LEVEL SECURITY")


# Maps our internal field names to the table's columns — single source of
# truth so the refresh job and the upsert statement can't drift apart.
# roe_5y_pct is reused for both markets — it's a 5Y average for IN (full
# multi-year history available) and a 4Y average for US (yfinance's free
# tier caps annual financials at 4 years); labelled accordingly in the UI,
# not claiming data we don't have.
FIELD_MAP = {
    "company_name":            "company_name",
    "sector_name":              "sector_name",
    "industry_name":            "industry_name",
    "market_cap_cr":            "market_cap_cr",
    "market_cap_usd_m":         "market_cap_usd_m",
    "pe_ratio":                 "pe_ratio",
    "roe_pct":                  "roe_pct",
    "roe_5y_pct":                "roe_5y_pct",
    "roce_pct":                  "roce_pct",
    "debt_to_equity_pct":       "debt_to_equity_pct",
    "promoter_holding_pct":     "promoter_holding_pct",
    "promoter_pledge_pct":      "promoter_pledge_pct",
    "insider_holding_pct":      "insider_holding_pct",
    "sales_growth_3y_pct":      "sales_growth_3y_pct",
    "sales_growth_5y_pct":      "sales_growth_5y_pct",
    "profit_growth_3y_pct":     "profit_growth_3y_pct",
    "profit_growth_5y_pct":     "profit_growth_5y_pct",
    "opm_pct":                   "opm_pct",
    "interest_coverage_ratio":  "interest_coverage_ratio",
    "ev_ebitda":                 "ev_ebitda",
    "price_to_sales":           "price_to_sales",
    "operating_cf_latest_cr":   "operating_cf_latest_cr",
}

_SELECT_COLS = ["symbol", "market", "company_name", "sector_name", "market_cap_cr", "market_cap_usd_m",
                "pe_ratio", "roe_pct", "roe_5y_pct", "roce_pct", "debt_to_equity_pct",
                "promoter_holding_pct", "promoter_pledge_pct", "insider_holding_pct",
                "sales_growth_3y_pct", "sales_growth_5y_pct",
                "profit_growth_3y_pct", "profit_growth_5y_pct",
                "opm_pct", "interest_coverage_ratio", "ev_ebitda", "price_to_sales",
                "operating_cf_latest_cr", "updated_at"]


def upsert(symbol: str, market: str, is_financial: bool, fields: dict):
    cols = ["symbol", "market", "is_financial", "updated_at"] + list(FIELD_MAP.values())
    placeholders = ", ".join(["%s"] * len(cols))
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("symbol", "market"))
    values = [symbol, market, is_financial, datetime.now(timezone.utc)] + [fields.get(k) for k in FIELD_MAP]

    with _conn() as conn:
        conn.execute(f"""
            INSERT INTO stock_fundamentals_cache ({", ".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT (symbol, market) DO UPDATE SET {update_clause}
        """, values)


def query_screen(screen: str, market: str = "IN") -> list[dict]:
    """
    Each screen is a hard AND-filter — a stock must pass every condition to
    appear, matching the "2-Screen System" design (combining loose +
    strict criteria into one screen produces zero/over-expensive results).
    """
    where, order = _SCREENS.get(screen, {}).get(market, (None, None))
    if where is None:
        return []

    with _conn() as conn:
        rows = conn.execute(f"""
            SELECT {", ".join(_SELECT_COLS)}
            FROM stock_fundamentals_cache
            WHERE market = %s AND {where}
            ORDER BY {order}
        """, [market]).fetchall()
        return [dict(zip(_SELECT_COLS, row)) for row in rows]


def get_sector(symbol: str, market: str = "IN") -> tuple[str | None, str | None]:
    """
    (sector_name, industry_name) for one symbol from the nightly-refreshed
    cache. A stock's sector classification doesn't change day to day, so
    this is a far more reliable source than re-scraping screener.in live on
    every prediction request — that live scrape has a 4h in-memory cache
    that also caches *failures* for the full TTL, so a single rate-limited
    request poisons the result for hours. This table persists across
    restarts and survives any individual scrape attempt failing.
    Returns (None, None) if the symbol isn't in the cache yet (e.g. it's
    outside the refresh job's universe, or hasn't run for this symbol yet).
    """
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT sector_name, industry_name FROM stock_fundamentals_cache "
                "WHERE symbol = %s AND market = %s",
                [symbol.upper(), market],
            ).fetchone()
            return (row[0], row[1]) if row else (None, None)
    except Exception:
        return (None, None)


def last_refreshed(market: str = "IN") -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) FROM stock_fundamentals_cache WHERE market = %s", [market]
        ).fetchone()
        return row[0].isoformat() if row and row[0] else None


# Quality Compounders and Multibagger Discovery don't depend on the new
# annual-P&L fields, so banks/NBFCs (where those fields are structurally
# absent — see fundamentals_refresh.py) aren't excluded from them. The
# 10-Bagger screen's OPM/Interest-Coverage checks naturally exclude
# financials since those columns are NULL for them (NULL fails any
# comparison), which is the correct behavior — those ratios aren't
# meaningful for a bank/NBFC anyway.
#
# US variants drop the promoter/pledge checks entirely — there's no
# "promoter" concept in US filings (insider_holding_pct is tracked as the
# closest informational analog but isn't gated on, since insider ownership
# norms differ structurally from Indian promoter-holding norms and aren't
# comparable apples-to-apples). Market cap thresholds use market_cap_usd_m
# with the same numeral as the IN Crore thresholds (e.g. 2000 -> $2000M),
# a deliberate simple re-scaling for US cap tiers, not a literal currency
# conversion of the India-calibrated thresholds.
_SCREENS: dict[str, dict[str, tuple[str, str]]] = {
    "quality_compounder": {
        "IN": (
            """
            market_cap_cr > 2000
            AND roe_5y_pct > 18
            AND roce_pct > 15
            AND debt_to_equity_pct < 50
            AND promoter_pledge_pct < 1
            AND promoter_holding_pct > 35
            AND sales_growth_5y_pct > 10
            AND profit_growth_5y_pct > 10
            AND pe_ratio < 35
            AND ev_ebitda < 20
            AND operating_cf_latest_cr > 0
            """,
            "roe_5y_pct DESC",
        ),
        "US": (
            # Uses 3Y growth, not 5Y — yfinance's free tier caps annual
            # financials at 4 years, so a true 5Y CAGR isn't computable for
            # US stocks at all (sales_growth_5y_pct/profit_growth_5y_pct are
            # always NULL for US rows). Substituting 3Y here rather than
            # gating on fields that could never pass.
            """
            market_cap_usd_m > 2000
            AND roe_5y_pct > 18
            AND roce_pct > 15
            AND debt_to_equity_pct < 50
            AND sales_growth_3y_pct > 10
            AND profit_growth_3y_pct > 10
            AND pe_ratio < 35
            AND ev_ebitda < 20
            AND operating_cf_latest_cr > 0
            """,
            "roe_5y_pct DESC",
        ),
    },
    "multibagger_discovery": {
        "IN": (
            """
            market_cap_cr > 300 AND market_cap_cr < 20000
            AND sales_growth_3y_pct > 15
            AND profit_growth_3y_pct > 15
            AND roce_pct > 12
            AND debt_to_equity_pct < 100
            AND promoter_pledge_pct < 2
            AND price_to_sales < 5
            AND pe_ratio < 50
            """,
            "profit_growth_3y_pct DESC",
        ),
        "US": (
            """
            market_cap_usd_m > 300 AND market_cap_usd_m < 20000
            AND sales_growth_3y_pct > 15
            AND profit_growth_3y_pct > 15
            AND roce_pct > 12
            AND debt_to_equity_pct < 100
            AND price_to_sales < 5
            AND pe_ratio < 50
            """,
            "profit_growth_3y_pct DESC",
        ),
    },
    "tenbagger_early": {
        "IN": (
            """
            market_cap_cr > 300 AND market_cap_cr < 15000
            AND sales_growth_3y_pct > 20
            AND profit_growth_3y_pct > 20
            AND roce_pct > 10
            AND roe_pct > 8
            AND debt_to_equity_pct < 100
            AND interest_coverage_ratio > 2
            AND promoter_pledge_pct < 2
            AND price_to_sales < 4
            AND pe_ratio < 60
            AND opm_pct > 8
            """,
            "sales_growth_3y_pct DESC",
        ),
        "US": (
            """
            market_cap_usd_m > 300 AND market_cap_usd_m < 15000
            AND sales_growth_3y_pct > 20
            AND profit_growth_3y_pct > 20
            AND roce_pct > 10
            AND roe_pct > 8
            AND debt_to_equity_pct < 100
            AND interest_coverage_ratio > 2
            AND price_to_sales < 4
            AND pe_ratio < 60
            AND opm_pct > 8
            """,
            "sales_growth_3y_pct DESC",
        ),
    },
}
