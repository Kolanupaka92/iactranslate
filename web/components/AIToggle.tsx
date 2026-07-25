"use client";

import type { Provider } from "@/lib/api";

export default function AIToggle({
  value,
  onChange,
  disabled,
}: {
  value: Provider;
  onChange: (p: Provider) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-start gap-2 text-sm">
      <input
        type="checkbox"
        checked={value === "anthropic"}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked ? "anthropic" : "rule")}
        className="mt-0.5 h-4 w-4 rounded border-neutral-300 accent-emerald-600 disabled:opacity-50 dark:border-neutral-700"
      />
      <span>
        <span className="block">Use AI (Claude) for classification &amp; sizing</span>
        <span className="block text-xs opacity-60">
          Optional — needs <code>ANTHROPIC_API_KEY</code> on the server. Off by
          default; every decision is re-checked by validation either way, and
          falls back to the deterministic rule engine if no key is set.
        </span>
      </span>
    </label>
  );
}
