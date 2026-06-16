/**
 * Market open/closed detection — computed locally from trading-hours rules,
 * not a live feed (NSE/BSE/NYSE don't expose a free, reliable status API).
 * Covers: weekday + trading-hours window (handles all weekends automatically)
 * + a holiday calendar. Fixed-date and algorithmically-computable holidays
 * (Easter-based Good Friday, "Nth weekday of month" US holidays) are exact
 * for any year. Lunar/variable Indian holidays (Holi, Diwali, Eid, etc.)
 * are NOT computable without an astronomical calendar — add them manually
 * to NSE_EXTRA_HOLIDAYS each year from the official NSE holiday circular.
 */

export type MarketKey = "IN" | "US" | "CRYPTO";

interface ZonedParts {
  weekday: string; // "Mon".."Sun"
  year: number;
  month: number; // 1-12
  day: number;
  minutesSinceMidnight: number;
  dateKey: string; // "YYYY-MM-DD"
}

function getZonedParts(date: Date, timeZone: string): ZonedParts {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const parts = fmt.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) map[p.type] = p.value;
  const year = Number(map.year);
  const month = Number(map.month);
  const day = Number(map.day);
  const hour = Number(map.hour) % 24;
  const minute = Number(map.minute);
  return {
    weekday: map.weekday,
    year,
    month,
    day,
    minutesSinceMidnight: hour * 60 + minute,
    dateKey: `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
  };
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

/** Nth weekday of a given month (e.g. 3rd Monday). weekday: 0=Sun..6=Sat */
function nthWeekdayOfMonth(year: number, month: number, weekday: number, n: number): string {
  const first = new Date(Date.UTC(year, month - 1, 1));
  const firstWeekday = first.getUTCDay();
  let day = 1 + ((weekday - firstWeekday + 7) % 7) + (n - 1) * 7;
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** Last weekday of a given month (e.g. last Monday of May). */
function lastWeekdayOfMonth(year: number, month: number, weekday: number): string {
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate(); // day 0 of next month = last day of this month
  const last = new Date(Date.UTC(year, month - 1, lastDay));
  const lastWeekday = last.getUTCDay();
  const day = lastDay - ((lastWeekday - weekday + 7) % 7);
  return `${year}-${pad(month)}-${pad(day)}`;
}

/** Easter Sunday via the anonymous Gregorian algorithm (Computus). */
function easterSunday(year: number): Date {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(year, month - 1, day));
}

/** Shift a fixed federal-style holiday to the nearest weekday if it falls on a weekend. */
function observedWeekday(year: number, month: number, day: number): string {
  const d = new Date(Date.UTC(year, month - 1, day));
  const dow = d.getUTCDay();
  if (dow === 6) d.setUTCDate(d.getUTCDate() - 1); // Saturday -> observed Friday
  if (dow === 0) d.setUTCDate(d.getUTCDate() + 1); // Sunday -> observed Monday
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function usMarketHolidays(year: number): Set<string> {
  const easter = easterSunday(year);
  const goodFriday = new Date(easter);
  goodFriday.setUTCDate(goodFriday.getUTCDate() - 2);
  const goodFridayKey = `${goodFriday.getUTCFullYear()}-${pad(goodFriday.getUTCMonth() + 1)}-${pad(goodFriday.getUTCDate())}`;

  return new Set([
    observedWeekday(year, 1, 1), // New Year's Day
    nthWeekdayOfMonth(year, 1, 1, 3), // MLK Day — 3rd Monday of Jan
    nthWeekdayOfMonth(year, 2, 1, 3), // Presidents Day — 3rd Monday of Feb
    goodFridayKey, // Good Friday
    lastWeekdayOfMonth(year, 5, 1), // Memorial Day — last Monday of May
    observedWeekday(year, 6, 19), // Juneteenth
    observedWeekday(year, 7, 4), // Independence Day
    nthWeekdayOfMonth(year, 9, 1, 1), // Labor Day — 1st Monday of Sept
    nthWeekdayOfMonth(year, 11, 4, 4), // Thanksgiving — 4th Thursday of Nov
    observedWeekday(year, 12, 25), // Christmas
  ]);
}

/**
 * NSE/BSE fixed-Gregorian-date holidays only (confidently computable).
 * Lunar/variable Hindu, Islamic, and other festival holidays (Holi, Diwali,
 * Eid, Dussehra, Mahashivratri, Ram Navami, Buddha Purnima, Guru Nanak
 * Jayanti, etc.) vary by year and require the official NSE holiday circular —
 * add them here manually each year.
 */
const NSE_EXTRA_HOLIDAYS: string[] = [
  // "2026-03-04",  // e.g. Holi — verify against official NSE circular and uncomment
];

function nseMarketHolidays(year: number): Set<string> {
  return new Set([
    `${year}-01-26`, // Republic Day
    `${year}-08-15`, // Independence Day
    `${year}-10-02`, // Gandhi Jayanti
    ...NSE_EXTRA_HOLIDAYS.filter((d) => d.startsWith(String(year))),
  ]);
}

export interface MarketStatus {
  isOpen: boolean;
  label: string;
}

const WEEKDAYS = new Set(["Mon", "Tue", "Wed", "Thu", "Fri"]);

export function getMarketStatus(market: MarketKey, now: Date = new Date()): MarketStatus {
  if (market === "CRYPTO") {
    return { isOpen: true, label: "Market Open" };
  }

  if (market === "IN") {
    const p = getZonedParts(now, "Asia/Kolkata");
    const isWeekday = WEEKDAYS.has(p.weekday);
    const isHoliday = nseMarketHolidays(p.year).has(p.dateKey);
    const inHours = p.minutesSinceMidnight >= 9 * 60 + 15 && p.minutesSinceMidnight < 15 * 60 + 30;
    const isOpen = isWeekday && !isHoliday && inHours;
    return { isOpen, label: isOpen ? "Market Open" : "Market Closed" };
  }

  // US
  const p = getZonedParts(now, "America/New_York");
  const isWeekday = WEEKDAYS.has(p.weekday);
  const isHoliday = usMarketHolidays(p.year).has(p.dateKey);
  const inHours = p.minutesSinceMidnight >= 9 * 60 + 30 && p.minutesSinceMidnight < 16 * 60;
  const isOpen = isWeekday && !isHoliday && inHours;
  return { isOpen, label: isOpen ? "Market Open" : "Market Closed" };
}
