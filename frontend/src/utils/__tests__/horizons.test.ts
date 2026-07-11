import { describe, it, expect } from "vitest";
import { HORIZON_INFO } from "../horizons";

describe("HORIZON_INFO — canonical horizon wording", () => {
  it("Short Term uses the canonical 1–5 trading days range", () => {
    const short = HORIZON_INFO.find(h => h.key === "short")!;
    expect(short.label).toBe("Short Term");
    expect(short.period).toBe("1–5 trading days");
  });

  it("Medium Term uses the canonical 2–4 weeks range", () => {
    const medium = HORIZON_INFO.find(h => h.key === "medium")!;
    expect(medium.label).toBe("Medium Term");
    expect(medium.period).toBe("2–4 weeks");
  });

  it("Long Term uses the canonical 3–6 months range, never the retired 6M–3 Years range", () => {
    const long = HORIZON_INFO.find(h => h.key === "long")!;
    expect(long.label).toBe("Long Term");
    expect(long.period).toBe("3–6 months");
    expect(long.period).not.toMatch(/3 Years/i);
    expect(long.period).not.toMatch(/6M/);
  });

  it("does not include a Core Investment horizon — that remains future/planned only, not a live signal", () => {
    expect(HORIZON_INFO.some(h => h.label.includes("Core Investment"))).toBe(false);
    expect(HORIZON_INFO.map(h => h.key)).toEqual(["short", "medium", "long"]);
  });
});
