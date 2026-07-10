"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/utils/api";
import { useAuth } from "@/lib/AuthContext";
import clsx from "clsx";

interface NavLink { href: string; label: string; accent?: boolean; color?: string }

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
  const { user } = useAuth();

  // Hide app nav links on the public landing page or login pages
  const isPublicPage = pathname === "/" || pathname === "/login" || pathname?.startsWith("/auth");
  if (isPublicPage || !user) return null;

  const isActive = (href: string) =>
    href === "/dashboard" ? pathname === "/dashboard" : pathname?.startsWith(href) ?? false;

  return (
    // key={pathname} forces a full remount of every link on each route
    // change instead of React patching classNames onto reused DOM nodes —
    // belt-and-suspenders against any stale active-state class surviving a
    // navigation, on top of the transition-timing fix above.
    <div key={pathname} className="flex items-center gap-1 py-1 text-sm text-gray-400">
      {links.map(({ href, label, accent, color }) => {
        const active = isActive(href);
        return (
          <Link
            key={href}
            href={href}
            className={clsx(
              // `transition-colors` here (a ~150ms fade on background/border/
              // text color) used to apply to the active <-> inactive swap
              // too, not just hover — so clicking to a new tab could freeze
              // a screenshot (or just a slow eye) mid-fade: the previous
              // tab's border/bg not yet faded out while the new page's
              // content had already fully rendered, reading as "the wrong
              // tab is highlighted." Which tab is current should snap
              // instantly since it's not a hover affordance, it's state —
              // transition-colors now only applies within the inactive
              // (hover-only) branch below, never to the active swap itself.
              "px-3 py-1.5 rounded-lg whitespace-nowrap border focus:outline-none",
              active
                ? "text-white font-semibold bg-white/10 border-brand-400/60"
                : clsx(
                    "border-transparent transition-colors",
                    color ? color : accent ? "font-medium text-green-400 hover:text-green-300" : "hover:text-white",
                    "hover:bg-white/5"
                  )
            )}
          >
            {label}
          </Link>
        );
      })}
      <AccuracyBadge />
    </div>
  );
}
