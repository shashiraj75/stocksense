import { describe, it, expect } from "vitest";
import { groupClosedTradesByMonth, groupClosedTradesByYear } from "../groupTradesByPeriod";

function trade(id: number, closed_at: string | null) {
  return { id, closed_at };
}

describe("groupClosedTradesByMonth", () => {
  it("groups trades into their calendar month/year", () => {
    const trades = [
      trade(1, "2026-09-03T10:00:00Z"),
      trade(2, "2026-09-01T10:00:00Z"),
      trade(3, "2026-08-15T10:00:00Z"),
    ];
    const groups = groupClosedTradesByMonth(trades);
    expect(groups).toHaveLength(2);
    expect(groups[0].label).toBe("September 2026");
    expect(groups[0].trades.map(t => t.id)).toEqual([1, 2]);
    expect(groups[1].label).toBe("August 2026");
    expect(groups[1].trades.map(t => t.id)).toEqual([3]);
  });

  it("preserves input order within a group and across groups (never re-sorts)", () => {
    // Deliberately out-of-chronological-order input — the function must
    // not silently re-sort; it only partitions in first-encountered order.
    const trades = [
      trade(1, "2026-08-01T00:00:00Z"),
      trade(2, "2026-09-01T00:00:00Z"),
      trade(3, "2026-08-15T00:00:00Z"),
    ];
    const groups = groupClosedTradesByMonth(trades);
    expect(groups.map(g => g.label)).toEqual(["August 2026", "September 2026"]);
    expect(groups[0].trades.map(t => t.id)).toEqual([1, 3]);
  });

  it("groups null closed_at into a single 'Date unavailable' bucket", () => {
    const trades = [
      trade(1, "2026-09-01T00:00:00Z"),
      trade(2, null),
      trade(3, null),
    ];
    const groups = groupClosedTradesByMonth(trades);
    expect(groups).toHaveLength(2);
    expect(groups[1].label).toBe("Date unavailable");
    expect(groups[1].trades.map(t => t.id)).toEqual([2, 3]);
  });

  it("groups an unparseable closed_at string into the same 'Date unavailable' bucket as null", () => {
    const trades = [trade(1, "not-a-date"), trade(2, null)];
    const groups = groupClosedTradesByMonth(trades);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Date unavailable");
    expect(groups[0].trades.map(t => t.id)).toEqual([1, 2]);
  });

  it("returns an empty array for an empty input, never throws", () => {
    expect(groupClosedTradesByMonth([])).toEqual([]);
  });

  it("handles a December/January year boundary correctly", () => {
    const trades = [
      trade(1, "2026-01-05T00:00:00Z"),
      trade(2, "2025-12-30T00:00:00Z"),
    ];
    const groups = groupClosedTradesByMonth(trades);
    expect(groups.map(g => g.label)).toEqual(["January 2026", "December 2025"]);
  });
});

describe("groupClosedTradesByYear", () => {
  it("groups trades into their calendar year only", () => {
    const trades = [
      trade(1, "2026-09-03T10:00:00Z"),
      trade(2, "2026-02-01T10:00:00Z"),
      trade(3, "2025-11-15T10:00:00Z"),
    ];
    const groups = groupClosedTradesByYear(trades);
    expect(groups).toHaveLength(2);
    expect(groups[0].label).toBe("2026");
    expect(groups[0].trades.map(t => t.id)).toEqual([1, 2]);
    expect(groups[1].label).toBe("2025");
    expect(groups[1].trades.map(t => t.id)).toEqual([3]);
  });

  it("groups null closed_at into a single 'Date unavailable' bucket", () => {
    const trades = [trade(1, "2026-01-01T00:00:00Z"), trade(2, null)];
    const groups = groupClosedTradesByYear(trades);
    expect(groups[1].label).toBe("Date unavailable");
  });
});
