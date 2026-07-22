"use client";

import type { Assessment, Finding, Severity } from "@/lib/api";

const BAND_COLOR: Record<Assessment["readiness"]["band"], string> = {
  ready: "bg-emerald-600",
  "minor-gaps": "bg-lime-600",
  "needs-work": "bg-amber-600",
  blocked: "bg-red-600",
};

const SEV_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-600 text-white",
  medium: "bg-amber-600 text-white",
  low: "bg-cyan-600 text-white",
  info: "bg-neutral-500 text-white",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 px-3 py-2 text-center dark:border-neutral-800">
      <div className="text-base font-semibold tabular-nums">{value}</div>
      <div className="mt-0.5 text-[0.7rem] opacity-60">{label}</div>
    </div>
  );
}

function FindingRow({ f }: { f: Finding }) {
  return (
    <div className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`rounded px-1.5 py-0.5 text-[0.65rem] font-bold uppercase tracking-wide ${SEV_STYLE[f.severity]}`}
        >
          {f.severity}
        </span>
        <span className="text-[0.65rem] uppercase tracking-wide opacity-50">
          {f.category}
        </span>
        <span className="flex-1 text-sm font-medium">{f.title}</span>
        {f.affected.length > 0 && (
          <span className="text-xs opacity-50">{f.affected.length} affected</span>
        )}
      </div>
      <p className="mt-1.5 text-sm opacity-80">{f.detail}</p>
      {f.recommendation && (
        <p className="mt-1 text-sm text-emerald-700 dark:text-emerald-400">
          → {f.recommendation}
        </p>
      )}
      {f.affected.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {f.affected.slice(0, 10).map((n) => (
            <span
              key={n}
              className="rounded bg-neutral-100 px-1.5 py-0.5 text-[0.7rem] dark:bg-neutral-800"
            >
              {n}
            </span>
          ))}
          {f.affected.length > 10 && (
            <span className="px-1 py-0.5 text-[0.7rem] opacity-50">
              +{f.affected.length - 10} more
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function AssessmentPanel({ a }: { a: Assessment }) {
  const r = a.readiness;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <div
          className={`flex h-16 w-16 flex-none items-center justify-center rounded-full text-xl font-bold text-white ${BAND_COLOR[r.band]}`}
        >
          {r.score}
        </div>
        <div>
          <div className="font-semibold capitalize">{r.band.replace("-", " ")}</div>
          <p className="text-sm opacity-70">{r.rationale}</p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="workloads" value={String(a.total_workloads)} />
        <Stat label="on / off" value={`${a.powered_on} / ${a.powered_off}`} />
        <Stat label="total vCPU" value={String(a.total_vcpu)} />
        <Stat label="RAM (GiB)" value={a.total_memory_gib.toLocaleString()} />
        <Stat label="storage (GiB)" value={a.total_storage_gib.toLocaleString()} />
        <Stat label="Win / Linux" value={`${a.windows_workloads} / ${a.linux_workloads}`} />
        <Stat label="unknown OS" value={String(a.unknown_os_workloads)} />
        <Stat label="util. coverage" value={`${a.utilization_coverage_pct.toFixed(0)}%`} />
      </div>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold">Findings ({a.findings.length})</h3>
        {a.findings.length === 0 ? (
          <p className="text-sm text-emerald-700 dark:text-emerald-400">
            No findings — the inventory is clean.
          </p>
        ) : (
          a.findings.map((f) => <FindingRow key={f.id} f={f} />)
        )}
      </div>
    </div>
  );
}
