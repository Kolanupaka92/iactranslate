"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import SignIn from "@/components/SignIn";
import { ApiError, acceptInvitationLink, logout, whoami, type Identity } from "@/lib/api";

/**
 * Where an invitation email lands: /invite?token=…
 *
 * The invitee may not have an account yet, so signing in (or registering)
 * happens on this page, and the token is held in memory meanwhile rather than
 * left in the address bar. Joining takes a click, never happens on load, so a
 * mail scanner fetching the link can't accept for anyone.
 */
export default function InvitePage() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const tokenRead = useRef(false);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [joined, setJoined] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIdentity(await whoami().catch(() => ({ authenticated: false, multi_tenant: true })));
  }, []);

  useEffect(() => {
    // Once only. The token is stripped from the address bar below, so a second
    // run (React runs effects twice in development) would find it gone.
    if (tokenRead.current) return;
    tokenRead.current = true;
    const t = new URLSearchParams(window.location.search).get("token");
    setToken(t);
    if (t) window.history.replaceState(null, "", window.location.pathname);
    void refresh();
  }, [refresh]);

  const accept = async () => {
    setState("busy");
    setError(null);
    try {
      const org = await acceptInvitationLink(token as string);
      setJoined(org.name);
      setState("done");
    } catch (err) {
      setState("error");
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  };

  if (token === undefined || identity === null) {
    return <main className="p-10 text-sm opacity-70">Loading…</main>;
  }

  if (!token) {
    return (
      <main className="mx-auto mt-24 w-full max-w-sm rounded-lg border border-slate-200 p-6">
        <h1 className="text-lg font-semibold text-slate-900">Join an organisation</h1>
        <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          This page needs the link from your invitation email. Open it again, or ask for a new
          invitation.
        </p>
      </main>
    );
  }

  if (!identity.authenticated) {
    return (
      <>
        <p className="mx-auto mt-10 w-full max-w-sm px-1 text-sm text-slate-600">
          You&apos;ve been invited to an organisation on IaCTranslate. Sign in, or create an
          account, with the address the invitation was sent to.
        </p>
        <SignIn onSignedIn={refresh} />
      </>
    );
  }

  return (
    <main className="mx-auto mt-24 w-full max-w-sm rounded-lg border border-slate-200 p-6">
      <h1 className="text-lg font-semibold text-slate-900">Join an organisation</h1>
      {state === "done" ? (
        <>
          <p role="status" className="mt-4 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            You&apos;ve joined {joined}.
          </p>
          <Link href="/console" className="mt-4 inline-block text-sm text-slate-600 underline">
            Go to the console
          </Link>
        </>
      ) : (
        <>
          <p className="mt-2 text-sm text-slate-500">Signed in as {identity.email}.</p>
          {error && (
            <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
              {/sign in with that address/.test(error) && (
                <>
                  {" "}
                  <button
                    type="button"
                    className="underline"
                    onClick={async () => {
                      await logout();
                      setError(null);
                      setState("idle");
                      await refresh();
                    }}
                  >
                    Sign out
                  </button>
                </>
              )}
            </p>
          )}
          <button
            type="button"
            onClick={accept}
            disabled={state === "busy"}
            className="mt-5 w-full rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {state === "busy" ? "Joining…" : "Accept invitation"}
          </button>
        </>
      )}
    </main>
  );
}
