"""
Market open/closed detection — single source of truth for the backend,
mirroring frontend/src/utils/marketHours.ts. Covers weekday + trading-hours
window + a holiday calendar (fixed-date holidays computed exactly; lunar/
regional Indian holidays sourced manually from the NSE circular each year).
"""
import datetime

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
ET = datetime.timezone(datetime.timedelta(hours=-4))  # approximation, ignores EST/EDT switch


def _easter_sunday(year: int) -> datetime.date:
    """Anonymous Gregorian algorithm (Computus)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _good_friday(year: int) -> datetime.date:
    return _easter_sunday(year) - datetime.timedelta(days=2)


def _nse_fixed_holidays(year: int) -> set[datetime.date]:
    return {
        datetime.date(year, 1, 26),   # Republic Day
        datetime.date(year, 4, 14),   # Dr. Babasaheb Ambedkar Jayanti
        _good_friday(year),
        datetime.date(year, 5, 1),    # Maharashtra Day
        datetime.date(year, 8, 15),   # Independence Day
        datetime.date(year, 10, 2),   # Gandhi Jayanti
        datetime.date(year, 12, 25),  # Christmas
    }


# Lunar/regional Indian holidays — no closed-form formula, sourced from the
# official NSE circular each year. Re-verify and refresh every December for
# the following year: https://www.nseindia.com/resources/exchange-communication-holidays
# 2026 list verified via https://zerodha.com/marketintel/holiday-calendar/ on 2026-06-22.
NSE_EXTRA_HOLIDAYS: set[str] = {
    "2026-01-15",  # Maharashtra Municipal Corporation elections (one-off, not recurring)
    "2026-03-03",  # Holi
    "2026-03-26",  # Shri Ram Navami
    "2026-03-31",  # Shri Mahavir Jayanti
    "2026-05-28",  # Bakri Eid
    "2026-06-26",  # Moharram
    "2026-09-14",  # Ganesh Chaturthi
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali — Balipratipada
    "2026-11-24",  # Guru Nanak Jayanti
}

# Muhurat trading — special ~1hr evening session NSE/BSE run on Diwali Laxmi
# Pujan despite that date otherwise being a holiday. Exact timing is
# announced by NSE only 1-2 weeks before Diwali — update once published.
MUHURAT_SESSIONS: list[dict] = [
    {"date": "2026-11-08", "start_min": 18 * 60, "end_min": 19 * 60 + 15},  # placeholder timing
]


def _us_fixed_holidays(year: int) -> set[datetime.date]:
    def observed(month: int, day: int) -> datetime.date:
        d = datetime.date(year, month, day)
        if d.weekday() == 5:  # Saturday -> observed Friday
            return d - datetime.timedelta(days=1)
        if d.weekday() == 6:  # Sunday -> observed Monday
            return d + datetime.timedelta(days=1)
        return d

    def nth_weekday(month: int, weekday: int, n: int) -> datetime.date:
        first = datetime.date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=offset + (n - 1) * 7)

    def last_weekday(month: int, weekday: int) -> datetime.date:
        if month == 12:
            next_month = datetime.date(year + 1, 1, 1)
        else:
            next_month = datetime.date(year, month + 1, 1)
        last_day = next_month - datetime.timedelta(days=1)
        offset = (last_day.weekday() - weekday) % 7
        return last_day - datetime.timedelta(days=offset)

    return {
        observed(1, 1),               # New Year's Day
        nth_weekday(1, 0, 3),          # MLK Day — 3rd Monday of Jan
        nth_weekday(2, 0, 3),          # Presidents Day — 3rd Monday of Feb
        _good_friday(year),
        last_weekday(5, 0),            # Memorial Day — last Monday of May
        observed(6, 19),               # Juneteenth
        observed(7, 4),                # Independence Day
        nth_weekday(9, 0, 1),          # Labor Day — 1st Monday of Sept
        nth_weekday(11, 3, 4),         # Thanksgiving — 4th Thursday of Nov
        observed(12, 25),              # Christmas
    }


def is_market_open(market: str) -> bool:
    if market == "IN":
        now = datetime.datetime.now(IST)
        date_key = now.strftime("%Y-%m-%d")

        for session in MUHURAT_SESSIONS:
            if session["date"] == date_key:
                minutes = now.hour * 60 + now.minute
                if session["start_min"] <= minutes < session["end_min"]:
                    return True

        if now.weekday() >= 5:
            return False
        if now.date() in _nse_fixed_holidays(now.year) or date_key in NSE_EXTRA_HOLIDAYS:
            return False
        return now.replace(hour=9, minute=15, second=0, microsecond=0) <= now <= now.replace(hour=15, minute=30, second=0, microsecond=0)

    elif market == "US":
        now = datetime.datetime.now(ET)
        if now.weekday() >= 5:
            return False
        if now.date() in _us_fixed_holidays(now.year):
            return False
        return now.replace(hour=9, minute=30, second=0, microsecond=0) <= now <= now.replace(hour=16, minute=0, second=0, microsecond=0)

    return False
