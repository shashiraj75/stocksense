/**
 * Set-password error messaging (2026-09-14).
 *
 * Root cause, confirmed directly via Supabase auth logs (not assumed): a
 * real invited user's password submission failed with Supabase's own
 * leaked-password rejection ("Password is known to be weak and easy to
 * guess" — PUT /user, 422, a legitimate security check, not a bug). The
 * page's blanket catch showed "request a new reset link and try again"
 * for every failure, including this one — sending the user down a false
 * trail, since their link/session was genuinely valid the whole time
 * (the same logs show invite verify + login already succeeded before the
 * password submission failed). Only a real session/token failure should
 * suggest requesting a new link.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AuthWeakPasswordError } from "@supabase/supabase-js";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}));

const updateUser = vi.fn();
const getUser = vi.fn();
vi.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      getUser: (...args: unknown[]) => getUser(...args),
      updateUser: (...args: unknown[]) => updateUser(...args),
    },
  },
}));

import SetPasswordPage from "../page";

async function fillAndSubmit() {
  const [pw, confirm] = screen.getAllByPlaceholderText(/password/i);
  fireEvent.change(pw, { target: { value: "correct-horse-battery" } });
  fireEvent.change(confirm, { target: { value: "correct-horse-battery" } });
  fireEvent.click(screen.getByRole("button", { name: /save password/i }));
}

describe("set-password error messaging", () => {
  beforeEach(() => {
    push.mockReset();
    updateUser.mockReset();
    getUser.mockReset();
    getUser.mockResolvedValue({ data: { user: { id: "u1", email: "test@example.com" } } });
  });

  it("shows a plain, actionable message for a leaked/weak password rejection — not the reset-link message", async () => {
    updateUser.mockResolvedValue({
      error: new AuthWeakPasswordError(
        "Password is known to be weak and easy to guess, please choose a different one.",
        422,
        ["pwned"] as any,
      ),
    });

    render(<SetPasswordPage />);
    await screen.findByText("Create your password");
    await fillAndSubmit();

    await waitFor(() => {
      expect(screen.getByText(/too easy to guess/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/request a new reset link/i)).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("still shows the generic reset-link message for a genuine session/token failure", async () => {
    updateUser.mockRejectedValue(new Error("invalid or expired session"));

    render(<SetPasswordPage />);
    await screen.findByText("Create your password");
    await fillAndSubmit();

    await waitFor(() => {
      expect(screen.getByText(/request a new reset link/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/too easy to guess/i)).not.toBeInTheDocument();
  });

  it("navigates on success without showing any error message", async () => {
    updateUser.mockResolvedValue({ error: null });

    render(<SetPasswordPage />);
    await screen.findByText("Create your password");
    await fillAndSubmit();

    await waitFor(() => {
      expect(push).toHaveBeenCalledWith("/accept-terms");
    });
    expect(screen.queryByText(/too easy to guess/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/request a new reset link/i)).not.toBeInTheDocument();
  });
});
