"use client";
import { useEffect } from "react";

/**
 * Exposes the navbar's actual rendered height as a CSS variable (--nav-h)
 * on the document root. The navbar's height varies a lot — it wraps
 * differently across breakpoints, the index strip and market status load
 * asynchronously, etc. — so any page that wants its own sticky element to
 * sit just below the navbar (rather than overlapping it) needs this rather
 * than a hardcoded offset.
 */
export function NavHeightObserver() {
  useEffect(() => {
    const nav = document.getElementById("site-nav");
    if (!nav) return;

    const update = () => {
      document.documentElement.style.setProperty("--nav-h", `${nav.offsetHeight}px`);
    };
    update();

    const observer = new ResizeObserver(update);
    observer.observe(nav);
    return () => observer.disconnect();
  }, []);

  return null;
}
