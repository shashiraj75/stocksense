"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

interface NavLink { href: string; label: string; accent?: boolean }

export function NavLinks({ links }: { links: NavLink[] }) {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

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
    </div>
  );
}
