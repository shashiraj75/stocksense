"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/utils/api";
import clsx from "clsx";

interface NavLink { href: string; label: string; accent?: boolean }

function AccuracyBadge() {
  const { data } = useQuery({
    queryKey: ["validation-summary"],
    queryFn: () => api.get<{ overall_accuracy_pct?: number; n_resolved?: number }>(
      "/api/validation/results?horizon=medium"
    ).then(r => r.data),
    staleTime: 30 * 60 * 1000,   // re-fetch every 30 min — this rarely changes
    retry: false,
  });

  const accuracy = data?.overall_accuracy_pct;
  const n = data?.n_resolved ?? 0;
  if (!accuracy || n < 10) return null;  // don't show until enough data

  const color = accuracy >= 65 ? "text-bull" : accuracy >= 55 ? "text-yellow-400" : "text-gray-400";

  return (
    <span
      className={clsx("text-xs font-semibold tabular-nums", color)}
      title={`Direction accuracy across ${n} resolved predictions (medium term)`}
    >
      ✓ {Math.round(accuracy)}% accurate
    </span>
  );
}

export function NavLinks({ links }: { links: NavLink[] }) {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname?.startsWith(href) ?? false;

  return (
    <div className="hidden lg:flex items-center gap-4 ml-2 text-sm text-gray-400">
      {links.map(({ href, label, accent }) => {
        const active = isActive(href);
        return (
          <Link
            key={href}
            href={href}
            className={clsx(
              "transition-colors pb-0.5",
              active
                ? "text-white font-bold border-b-2 border-white"
                : accent
                ? "font-medium text-green-400 hover:text-green-300"
                : "hover:text-white",
              active && accent && "text-green-400 border-green-400"
            )}
          >
            {label}
          </Link>
        );
      })}
      {/* Live accuracy badge — shows after 10+ resolved predictions */}
      <AccuracyBadge />
    </div>
  );
}
