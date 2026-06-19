import { NextRequest, NextResponse } from "next/server";

// Auth protection is handled client-side via AuthContext and useAuthGuard hook.
// This proxy is a pass-through — required by Next.js 16 for the proxy convention.
export function proxy(_request: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon).*)"],
};
