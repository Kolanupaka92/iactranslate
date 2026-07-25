"use client";

import type { Target } from "@/lib/api";

const TARGETS: { id: Target; label: string; blurb: string }[] = [
  { id: "aws", label: "AWS", blurb: "EC2 · VPC · Security Groups" },
  { id: "azure", label: "Azure", blurb: "VM · VNet · NSG" },
  { id: "gcp", label: "GCP", blurb: "Compute Engine · VPC · Firewalls" },
  { id: "oci", label: "OCI", blurb: "Compute · VCN · Network Security Groups" },
];

export default function TargetPicker({
  value,
  onChange,
  disabled,
}: {
  value: Target;
  onChange: (t: Target) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" role="radiogroup" aria-label="Target cloud">
      {TARGETS.map((t) => {
        const selected = t.id === value;
        return (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(t.id)}
            className={`rounded-lg border px-4 py-3 text-left transition-colors disabled:opacity-50 ${
              selected
                ? "border-emerald-600 bg-emerald-600/10 ring-1 ring-emerald-600"
                : "border-neutral-300 hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
            }`}
          >
            <div className="font-semibold">{t.label}</div>
            <div className="mt-0.5 text-xs opacity-70">{t.blurb}</div>
          </button>
        );
      })}
    </div>
  );
}
