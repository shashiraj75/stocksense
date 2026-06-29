"use client";
import { useState, useId } from "react";
import { ChevronDown } from "lucide-react";
import clsx from "clsx";

// A small, accessible expand/collapse primitive. Confirmed (Epic 005,
// Sprint #010/#011) that no collapsible/accordion/<details> component
// existed anywhere in this codebase before this one — built narrowly for
// Evidence Summary, reusable for any future similar need.
export function DisclosurePanel({
  label,
  defaultOpen = false,
  children,
}: {
  label: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-xs font-medium text-gray-400 hover:text-white transition-colors py-1"
      >
        <ChevronDown size={14} className={clsx("transition-transform", open && "rotate-180")} />
        {label}
      </button>
      {open && (
        <div id={panelId} className="mt-1.5">
          {children}
        </div>
      )}
    </div>
  );
}
