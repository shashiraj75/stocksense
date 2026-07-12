import { describe, it, expect } from "vitest";
import { getMarketStatus } from "../marketHours";

// Production truthfulness hotfix, Part F: nextEvent's displayed clock time
// was silently converted to the browser's local timezone
// (toLocaleTimeString with no `timeZone` option) while the underlying
// open/close instant is computed against the exchange's own timezone
// (Asia/Kolkata for NSE) — with no label distinguishing the two, a
// non-IST viewer could misread "Opens at 7:45 AM" as NSE's own local
// open time rather than their own. This must never render an unlabeled
// bare time again.
describe("getMarketStatus — next-event time is timezone-unambiguous", () => {
  it("NSE's next-event label carries an explicit timezone marker, not a bare time", () => {
    // A fixed Sunday — NSE is reliably closed, so nextEventLabel is populated.
    const sunday = new Date("2026-07-12T06:00:00Z");
    const status = getMarketStatus("IN", sunday);
    expect(status.nextEventLabel).toBeTruthy();
    // Accept any of the common Intl short timezone tokens (GMT+N, UTC, or a
    // named abbreviation like IST/PDT) — the point is *some* explicit
    // marker is present, never a bare "7:45 AM · Mon, 13 Jul" with nothing
    // to say whose 7:45 AM it is.
    expect(status.nextEventLabel).toMatch(/GMT|UTC|[A-Z]{2,5}$|[A-Z]{2,5}\s*[+-]\d/);
  });

  it("never visually reads as a bare, unlabeled NSE open time", () => {
    const sunday = new Date("2026-07-12T06:00:00Z");
    const status = getMarketStatus("IN", sunday);
    // The old bug's exact symptom: a time string with no timezone
    // information trailing it at all.
    expect(status.nextEventLabel).not.toMatch(/^Opens at \d{1,2}:\d{2} (AM|PM) · /);
  });

  it("US market next-event label is also timezone-unambiguous", () => {
    const sunday = new Date("2026-07-12T06:00:00Z");
    const status = getMarketStatus("US", sunday);
    expect(status.nextEventLabel).toBeTruthy();
    expect(status.nextEventLabel).toMatch(/GMT|UTC|[A-Z]{2,5}$|[A-Z]{2,5}\s*[+-]\d/);
  });

  it("underlying open/closed calculation is unchanged — NSE is closed on a Sunday", () => {
    const sunday = new Date("2026-07-12T06:00:00Z");
    expect(getMarketStatus("IN", sunday).isOpen).toBe(false);
  });
});
