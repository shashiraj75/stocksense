import { NextRequest, NextResponse } from "next/server";

// Middleware intentionally kept minimal — auth protection is handled
// client-side via AuthContext and per-page redirects.
// A full server-side middleware with Supabase getUser() caused ERR_TOO_MANY_REDIRECTS
// on Vercel's edge runtime due to cookie sync issues with @supabase/ssr v0.12.
export function middleware(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon).*)"],
};
