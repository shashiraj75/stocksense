import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

// Live generation-progress badge (header). page.tsx mounts through live
// data-fetching (useQuery, useRouter, marketHours checks) that isn't
// practically mountable in isolation here — same rationale as the other
// picks-page containment tests — so this locks in the required wiring as
// source-text assertions against the real file.
const pageSource = readFileSync(
  path.resolve(process.cwd(), "src/app/picks/page.tsx"),
  "utf-8",
);

describe("Live generation-progress badge", () => {
  it("polls /api/picks/status only while /api/picks/daily reports generating: true", () => {
    expect(pageSource).toContain('const isGenerating = !!(rawDailyPicksData as any)?.generating;');
    expect(pageSource).toContain('queryFn: () => api.get(`/api/picks/status?market=${market}`).then(r => r.data),');
    expect(pageSource).toContain("enabled: isGenerating,");
    expect(pageSource).toContain("refetchInterval: isGenerating ? 15_000 : false,");
  });

  it("never fabricates a percentage when total is missing or zero", () => {
    expect(pageSource).toContain(
      'const hasCounts = typeof processed === "number" && typeof total === "number" && total > 0;',
    );
    expect(pageSource).toContain('Updating{hasCounts ? ` — ${processed}/${total} (~${pct}%)` : "…"}');
  });

  it("badge only renders while isGenerating is true", () => {
    expect(pageSource).toContain("{isGenerating && (() => {");
  });

  it("RefreshCw icon is imported for the badge", () => {
    expect(pageSource).toMatch(/import\s*{[^}]*RefreshCw[^}]*}\s*from "lucide-react";/);
  });
});
