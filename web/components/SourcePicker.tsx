"use client";

import type { Source } from "@/lib/api";

const SOURCES: { id: Source; label: string }[] = [
  { id: "auto", label: "Auto-detect" },
  { id: "vmware", label: "VMware (RVTools)" },
  { id: "hyperv", label: "Microsoft Hyper-V" },
  { id: "kubernetes", label: "Kubernetes (kubectl JSON)" },
  { id: "generic", label: "CMDB / spreadsheet" },
  { id: "cloud", label: "Existing cloud fleet" },
];

export default function SourcePicker({
  value,
  onChange,
  disabled,
}: {
  value: Source;
  onChange: (s: Source) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block opacity-70">Discovery source</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as Source)}
        className="w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-emerald-600 disabled:opacity-50 dark:border-neutral-700"
      >
        {SOURCES.map((s) => (
          <option key={s.id} value={s.id} className="bg-white dark:bg-neutral-900">
            {s.label}
          </option>
        ))}
      </select>
    </label>
  );
}
