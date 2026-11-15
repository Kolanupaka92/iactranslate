"use client";

import { useState } from "react";

import { ApiError, deleteAccount } from "@/lib/api";

/**
 * Self-service account deletion.
 *
 * Deliberately not a one-click control. It is irreversible and takes every
 * project the user owns with it, so it demands the password — a stolen
 * session can already read the estate; it must not also be able to make the
 * loss permanent — and it says exactly what it is about to remove before it
 * removes it.
 */
export default function DeleteAccount({ onDeleted }: { onDeleted: () => void }) {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="ml-2 underline opacity-70 hover:opacity-100"
      >
        Delete account
      </button>
    );
  }

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteAccount(password);
      onDeleted();
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 403
          ? "That password is incorrect."
          : "Could not delete the account. Try again, and report it if it recurs.",
      );
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 max-w-md rounded-lg border border-red-600/40 bg-red-500/5 p-4 text-left text-sm">
      <p className="font-semibold text-red-800 dark:text-red-300">Delete this account?</p>
      <p className="mt-1 leading-relaxed opacity-80">
        This removes your account, every project you own and its generated files, and any
        access you have to other people&rsquo;s projects. It cannot be undone. Enter your
        password to confirm.
      </p>
      <input
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        placeholder="Current password"
        className="mt-3 w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700"
      />
      {error && <p className="mt-2 text-red-800 dark:text-red-300">{error}</p>}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={submit}
          disabled={busy || !password}
          className="rounded-md bg-red-700 px-3 py-1.5 font-semibold text-white hover:bg-red-600 disabled:opacity-50"
        >
          {busy ? "Deleting…" : "Delete permanently"}
        </button>
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            setPassword("");
            setError(null);
          }}
          disabled={busy}
          className="rounded-md border border-neutral-300 px-3 py-1.5 dark:border-neutral-700"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
