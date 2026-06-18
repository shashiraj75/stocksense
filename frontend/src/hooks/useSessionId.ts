"use client";
import { useMemo } from "react";

function generateId(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export function useSessionId(): string {
  return useMemo(() => {
    if (typeof window === "undefined") return "ssr";
    let id = localStorage.getItem("paper_session_id");
    if (!id) {
      id = generateId();
      localStorage.setItem("paper_session_id", id);
    }
    return id;
  }, []);
}
