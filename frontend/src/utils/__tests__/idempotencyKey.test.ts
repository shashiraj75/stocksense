import { describe, it, expect } from "vitest";
import { generateBuyIdempotencyKey } from "@/utils/idempotencyKey";

describe("generateBuyIdempotencyKey", () => {
  it("generates a key within the backend's allowed length bounds (8-128 chars)", () => {
    const key = generateBuyIdempotencyKey();
    expect(key.length).toBeGreaterThanOrEqual(8);
    expect(key.length).toBeLessThanOrEqual(128);
  });

  it("only uses the backend's allowed character set (letters, digits, -, _)", () => {
    const key = generateBuyIdempotencyKey();
    expect(key).toMatch(/^[a-zA-Z0-9_-]+$/);
  });

  it("generates a different key on each call", () => {
    const a = generateBuyIdempotencyKey();
    const b = generateBuyIdempotencyKey();
    expect(a).not.toBe(b);
  });
});
