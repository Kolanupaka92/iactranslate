"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import HomeLink from "@/components/HomeLink";
import { ApiError, resetPassword } from "@/lib/api";

/**
 * Where the emailed reset link lands: /reset-password?token=…
 *
 * The token is read once and then removed from the address bar, so it does
 * not sit in browser history or appear in a screenshot of the page. It is
 * single-use and expires in an hour regardless.
 */
export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const tokenRead = useRef(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    // Once only. The token is stripped from the address bar below, so a second
    // run (React runs effects twice in development) would find it gone.
    if (tokenRead.current) return;
    tokenRead.current = true;
    const t = new URLSearchParams(window.location.search).get("token");
    setToken(t);
    if (t) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resetPassword(token as string, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto mt-24 w-full max-w-sm rounded-lg border border-slate-200 p-6">
      <HomeLink className="mb-4 inline-block text-slate-500" />
      <h1 className="text-lg font-semibold text-slate-900">Choose a new password</h1>

      {token === undefined ? null : done ? (
        <>
          <p role="status" className="mt-4 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Your password has been changed, and every session on the account has been signed out.
          </p>
          <Link href="/console" className="mt-4 inline-block text-sm text-slate-600 underline">
            Sign in with the new password
          </Link>
        </>
      ) : !token ? (
        <>
          <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            This page needs the link from your reset email. Open the link from the email again, or
            request a new one.
          </p>
          <Link href="/console" className="mt-4 inline-block text-sm text-slate-600 underline">
            Back to sign in
          </Link>
        </>
      ) : (
        <form onSubmit={submit} className="mt-5 flex flex-col gap-3">
          <label className="text-sm text-slate-700">
            New password
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
            />
            <span className="mt-1 block text-xs text-slate-500">At least 12 characters.</span>
          </label>
          <label className="text-sm text-slate-700">
            Confirm new password
            <input
              type="password"
              required
              minLength={12}
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
            />
          </label>

          {error && (
            <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
              {/expired|invalid/i.test(error) && (
                <>
                  {" "}
                  <Link href="/console" className="underline">
                    Request a new link
                  </Link>
                  .
                </>
              )}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Working…" : "Set new password"}
          </button>
        </form>
      )}
    </main>
  );
}
