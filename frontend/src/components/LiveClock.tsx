"use client";
import { useEffect, useState } from "react";

export function LiveClock({ inline }: { inline?: boolean }) {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!now) return null;

  // Product Integrity Workstream #001 — this clock previously formatted
  // with the "en-IN" locale (Indian date/number conventions) but NO
  // `timeZone` option, so it silently rendered the BROWSER's local wall
  // clock — not IST — while looking like an IST clock. A user outside IST
  // (e.g. UAE, US) would see a time here that quietly disagreed with other
  // explicitly-IST-converted timestamps elsewhere on the page (e.g. Daily
  // Picks' "Updated" label), with no label explaining the difference. This
  // is the confirmed root cause of the reported header-vs-Daily-Picks
  // "67h ago" inconsistency. Fixed by explicitly converting to IST and
  // labeling it, so this clock's timezone basis is real and disclosed.
  const date = now.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    weekday: "short", day: "2-digit", month: "short", year: "numeric",
  });
  const time = now.toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
  });

  if (inline) {
    return (
      <div className="text-xs leading-5">
        <div className="font-medium text-gray-300">{time} <span className="text-[10px] text-gray-500">IST</span></div>
        <div className="text-gray-500">{date}</div>
      </div>
    );
  }

  return (
    <div className="text-right text-xs text-gray-400 leading-5">
      <div className="font-medium text-gray-300">{time} <span className="text-[10px] text-gray-500">IST</span></div>
      <div>{date}</div>
    </div>
  );
}
