"use client";

import { useState } from "react";

import { ApiError, resendVerification, type Identity } from "@/lib/api";

/**
 * Shown while the signed-in account's address is unverified. Says a link was
 * sent only when the server can actually email one.
 */
export default function VerifyEmailBanner({ identity }: { identity: Identity }) {
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState<string | null>(null);

  const resend = async () => {
    setState("sending");
    try {
      await resendVerification();
      setState("sent");
    } catch (err) {
      setState("error");
      setMessage(err instanceof ApiError ? err.message : "Couldn't send the link.");
    }
  };

  const tone = identity.verification_required
    ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
    : "border-slate-300 bg-slate-50 text-slate-800 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200";

  return (
    <div role="status" className={`mb-6 rounded-lg border px-4 py-3 text-sm ${tone}`}>
      <strong className="font-semibold">Confirm your email address.</strong>{" "}
      {identity.verification_email ? (
        <>
          We sent a link to {identity.email}; it works for 24 hours.
          {identity.verification_required && " You can create projects once it's confirmed."}{" "}
          {state === "sent" ? (
            <span>A new link is on its way.</span>
          ) : (
            <button
              type="button"
              onClick={resend}
              disabled={state === "sending"}
              className="underline disabled:opacity-50"
            >
              {state === "sending" ? "Sending…" : "Send a new link"}
            </button>
          )}
          {state === "error" && message && <span className="ml-2">{message}</span>}
        </>
      ) : (
        <>This server can&apos;t send email yet, so ask your administrator to confirm it.</>
      )}
    </div>
  );
}
