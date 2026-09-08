"use client";
import { useState, useRef, useEffect } from "react";
import { useAuth } from "@/lib/AuthContext";
import { useRouter } from "next/navigation";
import { LogOut, User } from "lucide-react";
import Link from "next/link";

export function UserMenu() {
  const { user, signOut, loading } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();

  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  if (loading) return <div className="w-8 h-8 rounded-full bg-dark-border animate-pulse" />;

  if (!user) {
    return (
      <Link
        href="/login"
        className="shrink-0 px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-600 text-white text-xs font-medium transition-colors"
      >
        Sign In
      </Link>
    );
  }

  // First name instead of two-letter initials (user request, 2026-09-08) —
  // `first_name` is the field accept-terms/page.tsx actually collects and
  // persists to user_metadata; fall back to a full_name's first word, then
  // the email's local-part, then a generic "Account" so this never renders
  // blank for a legacy user who signed up before first_name was collected.
  const firstName =
    user.user_metadata?.first_name?.trim()
    || user.user_metadata?.full_name?.trim()?.split(/\s+/)[0]
    || user.email?.split("@")[0]
    || "Account";
  const avatarUrl = user.user_metadata?.avatar_url;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 pl-1 pr-3 py-1 rounded-full border-2 border-dark-border hover:border-brand-500 transition-colors bg-brand-500/10 text-gray-200 text-xs font-semibold"
      >
        <span className="w-6 h-6 rounded-full overflow-hidden shrink-0 flex items-center justify-center bg-brand-500/20 text-brand-400 font-bold">
          {avatarUrl
            ? <img src={avatarUrl} alt="avatar" className="w-full h-full object-cover" />
            : firstName.slice(0, 1).toUpperCase()}
        </span>
        <span className="max-w-[100px] truncate">{firstName}</span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-56 bg-dark-card border border-dark-border rounded-xl shadow-xl overflow-hidden z-50">
          <div className="px-4 py-3 border-b border-dark-border">
            <p className="text-xs text-gray-400">Signed in as</p>
            <p className="text-sm font-medium text-white truncate">{user.email}</p>
          </div>
          <button
            onClick={async () => { await signOut(); setOpen(false); router.push("/"); }}
            className="w-full flex items-center gap-2 px-4 py-3 text-sm text-red-400 hover:bg-dark-border/50 transition-colors"
          >
            <LogOut size={14} />
            Sign Out
          </button>
        </div>
      )}
    </div>
  );
}
