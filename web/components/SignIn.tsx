"use client";

import { useEffect, useState } from "react";

import { ApiError, authMethods, forgotPassword, login, register, samlLoginUrl } from "@/lib/api";

type Mode = "login" | "register" | "forgot";

const TITLES: Record<Mode, string> = {
  login: "Sign in",
  register: "Create an account",
  forgot: "Reset your password",
};

/**
 * Sign-in gate, rendered only when the API reports `multi_tenant: true`.
 * Single-tenant deployments (the CLI/self-host default) never see this.
 */
export default function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetSent, setResetSent] = useState(false);
  const [sso, setSso] = useState(false);

  useEffect(() => {
    void authMethods()
      .then((m) => setSso(m.saml))
      .catch(() => setSso(false));
    // The SAML endpoint sends a refused sign-in back here with ?sso=failed.
    // The reason is in the server log; the page says only that it failed.
    const params = new URLSearchParams(window.location.search);
    if (params.get("sso") === "failed") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError("Single sign-on didn't complete. Try again, or ask your administrator.");
      params.delete("sso");
      const rest = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : ""));
    }
  }, []);

  const switchTo = (next: Mode) => {
    setMode(next);
    setError(null);
    setResetSent(false);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "forgot") {
        await forgotPassword(email);
        // Same message whether or not the account exists: the API does not
        // say, and the page must not either.
        setResetSent(true);
        return;
      }
      await (mode === "login" ? login(email, password) : register(email, password));
      onSignedIn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto mt-24 w-full max-w-sm rounded-lg border border-slate-200 p-6">
      <h1 className="text-lg font-semibold text-slate-900">{TITLES[mode]}</h1>
      <p className="mt-1 text-sm text-slate-500">
        {mode === "forgot"
          ? "Enter your account email and we'll send a link to choose a new password."
          : "Your projects and inventory are private to your account."}
      </p>

      {sso && mode === "login" && (
        <>
          <a
            href={samlLoginUrl}
            className="mt-5 block rounded border border-slate-300 px-3 py-2 text-center text-sm font-medium text-slate-800 hover:border-slate-900"
          >
            Continue with single sign-on
          </a>
          <p className="mt-4 text-center text-xs text-slate-400">or with email and password</p>
        </>
      )}

      {mode === "forgot" && resetSent ? (
        <p role="status" className="mt-5 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          If an account exists for {email}, a reset link is on its way. It expires in one hour.
          Check your spam folder if it doesn&apos;t arrive in a few minutes.
        </p>
      ) : (
        <form onSubmit={submit} className="mt-5 flex flex-col gap-3">
          <label className="text-sm text-slate-700">
            Email
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
            />
          </label>

          {mode !== "forgot" && (
            <label className="text-sm text-slate-700">
              Password
              <input
                type="password"
                required
                minLength={12}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
              />
              {mode === "register" && (
                <span className="mt-1 block text-xs text-slate-500">At least 12 characters.</span>
              )}
            </label>
          )}

          {error && (
            <p role="alert" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy
              ? "Working…"
              : mode === "login"
                ? "Sign in"
                : mode === "register"
                  ? "Create account"
                  : "Email me a reset link"}
          </button>
        </form>
      )}

      <div className="mt-4 flex flex-col items-start gap-2">
        {mode === "login" && (
          <button
            type="button"
            onClick={() => switchTo("forgot")}
            className="text-sm text-slate-600 underline"
          >
            Forgot your password?
          </button>
        )}
        <button
          type="button"
          onClick={() => switchTo(mode === "register" ? "login" : mode === "forgot" ? "login" : "register")}
          className="text-sm text-slate-600 underline"
        >
          {mode === "login" ? "Need an account? Create one" : "Back to sign in"}
        </button>
      </div>
    </div>
  );
}
