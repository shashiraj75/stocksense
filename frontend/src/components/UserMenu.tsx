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

  const initials = user.email?.slice(0, 2).toUpperCase() ?? "U";
  const avatarUrl = user.user_metadata?.avatar_url;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-8 h-8 rounded-full overflow-hidden border-2 border-dark-border hover:border-brand-500 transition-colors flex items-center justify-center bg-brand-500/20 text-brand-400 text-xs font-bold"
      >
        {avatarUrl
          ? <img src={avatarUrl} alt="avatar" className="w-full h-full object-cover" />
          : initials}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-56 bg-dark-card border border-dark-border rounded-xl shadow-xl overflow-hidden z-50">
          <div className="px-4 py-3 border-b border-dark-border">
            <p className="text-xs text-gray-400">Signed in as</p>
            <p className="text-sm font-medium text-white truncate">{user.email}</p>
          </div>
          <button
            onClick={async () => { await signOut(); setOpen(false); router.push("/login"); }}
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
