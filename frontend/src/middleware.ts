import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";

const PUBLIC_PATHS = ["/", "/login", "/auth"];

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths and static assets without touching Supabase at all
  if (
    PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/")) ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon")
  ) {
    return NextResponse.next();
  }

  // Must be `let` — Supabase SSR's setAll reassigns this when refreshing tokens
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          // Must set on request AND reassign supabaseResponse so refreshed
          // tokens are written back to the browser cookie jar
          cookiesToSet.forEach(({ name, value, options }) =>
            request.cookies.set(name, value, options)
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  // getUser() makes a server-side call to verify the token — getSession() is
  // unreliable in middleware and can return stale data from the cookie
  const { data: { user } } = await supabase.auth.getUser();

  // No session → redirect to login, copying any refreshed Supabase cookies
  if (!user) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    const redirectResponse = NextResponse.redirect(url);
    supabaseResponse.cookies.getAll().forEach((c) =>
      redirectResponse.cookies.set(c.name, c.value, { path: "/" })
    );
    return redirectResponse;
  }

  // Logged in but terms not yet accepted → redirect to accept-terms
  const termsAccepted = user.user_metadata?.terms_accepted === true;
  if (!termsAccepted && pathname !== "/accept-terms") {
    const url = request.nextUrl.clone();
    url.pathname = "/accept-terms";
    const redirectResponse = NextResponse.redirect(url);
    supabaseResponse.cookies.getAll().forEach((c) =>
      redirectResponse.cookies.set(c.name, c.value, { path: "/" })
    );
    return redirectResponse;
  }

  // All good — return supabaseResponse (not a new NextResponse) so any
  // refreshed session cookies are preserved in the browser
  return supabaseResponse;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon).*)"],
};
