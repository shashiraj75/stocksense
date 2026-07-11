import { describe, it, expect } from "vitest";
import { normalizeDisplaySector } from "../sectorDisplay";

describe("normalizeDisplaySector", () => {
  it("JSLL-like case: raw sector 'Consumer Services' + industry 'Wellness' displays as Healthcare", () => {
    expect(normalizeDisplaySector("Consumer Services", "Wellness")).toBe("Healthcare");
  });

  it("industry containing Hospitals displays as Healthcare", () => {
    expect(normalizeDisplaySector("Consumer Services", "Hospitals")).toBe("Healthcare");
  });

  it("industry containing Healthcare displays as Healthcare", () => {
    expect(normalizeDisplaySector("Consumer Discretionary", "Healthcare Equipment & Supplies")).toBe("Healthcare");
  });

  it("industry containing Medical Care displays as Healthcare", () => {
    expect(normalizeDisplaySector("Consumer Services", "Medical Care Facilities")).toBe("Healthcare");
  });

  it("industry containing Diagnostics displays as Healthcare", () => {
    expect(normalizeDisplaySector("Consumer Services", "Diagnostics & Research")).toBe("Healthcare");
  });

  it("industry containing Pharma displays as Healthcare", () => {
    expect(normalizeDisplaySector("Materials", "Pharmaceuticals")).toBe("Healthcare");
    expect(normalizeDisplaySector("Materials", "Pharma & Biotech")).toBe("Healthcare");
  });

  it("is case-insensitive on the industry keyword match", () => {
    expect(normalizeDisplaySector("Consumer Services", "WELLNESS")).toBe("Healthcare");
    expect(normalizeDisplaySector("Consumer Services", "wellness clinics")).toBe("Healthcare");
  });

  it("a normal Consumer Services stock with no healthcare/wellness industry stays Consumer Services", () => {
    expect(normalizeDisplaySector("Consumer Services", "Hotels & Resorts")).toBe("Consumer Services");
    expect(normalizeDisplaySector("Consumer Services", "Retail")).toBe("Consumer Services");
  });

  it("does not treat unrelated sectors as healthcare just because industry is missing", () => {
    expect(normalizeDisplaySector("IT", null)).toBe("IT");
    expect(normalizeDisplaySector("Energy", undefined)).toBe("Energy");
  });

  it("passes through a sector that already says Healthcare, even with an unrelated/missing industry", () => {
    expect(normalizeDisplaySector("Healthcare", "Something Else")).toBe("Healthcare");
    expect(normalizeDisplaySector("Healthcare", null)).toBe("Healthcare");
  });

  it("returns null (not 'Other') when both sector and industry are missing — callers apply their own Other fallback", () => {
    expect(normalizeDisplaySector(null, null)).toBeNull();
    expect(normalizeDisplaySector(undefined, undefined)).toBeNull();
    expect(normalizeDisplaySector("", "")).toBeNull();
  });

  it("does not crash on whitespace-only values", () => {
    expect(normalizeDisplaySector("   ", "   ")).toBeNull();
  });
});
