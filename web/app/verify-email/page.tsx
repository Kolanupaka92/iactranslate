"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import HomeLink from "@/components/HomeLink";
import { ApiError, verifyEmail } from "@/lib/api";

/**
 * Where the emailed verification link lands: /verify-email?token=…
 *
 * Confirms with one click rather than on load. Mail scanners and link
 * previewers fetch URLs in messages; a page that verified on GET (or on
 * mount) would let a scanner confirm an address its owner never saw.
 */
export default function VerifyEmailPage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const tokenRead = useRef(false);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Once only. The token is stripped from the address bar below, so a second
    // run (React runs effects twice in development) would find it gone.
    if (tokenRead.current) return;
    tokenRead.current = true;
    const t = new URLSearchParams(window.location.search).get("token");
    setToken(t);
    if (t) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  const confirm = async () => {
    setState("busy");
    setError(null);
    try {
      await verifyEmail(token as string);
      setState("done");
    } catch (err) {
      setState("error");
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  };

  return (
    <main className="mx-auto mt-24 w-full max-w-sm rounded-lg border border-slate-200 p-6">
      <HomeLink className="mb-4 inline-block text-slate-500" />
      <h1 className="text-lg font-semibold text-slate-900">Confirm your email</h1>

      {token === undefined ? null : state === "done" ? (
        <>
          <p role="status" className="mt-4 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            Your email address is confirmed.
          </p>
          <Link href="/console" className="mt-4 inline-block text-sm text-slate-600 underline">
            Go to the console
          </Link>
        </>
      ) : !token ? (
        <>
          <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            This page needs the link from your confirmation email. Open it again, or send a new
            one from the console.
          </p>
          <Link href="/console" className="mt-4 inline-block text-sm text-slate-600 underline">
            Go to the console
          </Link>
        </>
      ) : (
        <>
          <p className="mt-2 text-sm text-slate-500">
            Confirm that this address belongs to you.
          </p>
          {error && (
            <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}.{" "}
              <Link href="/console" className="underline">
                Send a new link from the console
              </Link>
              .
            </p>
          )}
          <button
            type="button"
            onClick={confirm}
            disabled={state === "busy"}
            className="mt-5 w-full rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {state === "busy" ? "Confirming…" : "Confirm email address"}
          </button>
        </>
      )}
    </main>
  );
}
