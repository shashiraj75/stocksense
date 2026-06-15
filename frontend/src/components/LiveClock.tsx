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

  const date = now.toLocaleDateString("en-IN", {
    weekday: "short", day: "2-digit", month: "short", year: "numeric",
  });
  const time = now.toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true,
  });

  if (inline) {
    return (
      <span className="text-xs text-gray-500">
        {time} · {date}
      </span>
    );
  }

  return (
    <div className="text-right text-xs text-gray-400 leading-5">
      <div className="font-medium text-gray-300">{time}</div>
      <div>{date}</div>
    </div>
  );
}
