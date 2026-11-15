"use client";

import type { ProjectSummary, Target } from "@/lib/api";

/**
 * What a signed-in user lands on: the state of everything they've analysed.
 *
 * Aggregated client-side from the project list the console already fetches,
 * so it costs no request. Every figure is a sum of numbers the pipeline
 * produced; nothing here is estimated from the estimates.
 *
 * The one thing it will not show is a saving. Savings need the customer's
 * current infrastructure spend — VMware licensing, datacenter, power,
 * amortisation — which no inventory export contains and which the product
 * refuses to invent. That slot is present and says why it's empty, because a
 * dashboard with a suspiciously missing number invites the wrong guess.
 */

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const CLOUD_LABEL: Record<Target, string> = {
  aws: "AWS",
  azure: "Azure",
  gcp: "GCP",
  oci: "OCI",
  digitalocean: "DigitalOcean",
  nutanix: "Nutanix AHV",
  proxmox: "Proxmox VE",
};

function Stat({
  label,
  value,
  note,
  tone = "neutral",
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "neutral" | "good" | "warn";
}) {
  const toneClass = {
    neutral: "",
    good: "text-emerald-800 dark:text-emerald-300",
    warn: "text-amber-800 dark:text-amber-300",
  }[tone];
  return (
    <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
      <div className="text-xs uppercase opacity-60">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {note ? <div className="mt-1 text-xs leading-snug opacity-70">{note}</div> : null}
    </div>
  );
}

export default function Dashboard({
  projects,
  onOpen,
}: {
  projects: ProjectSummary[];
  onOpen: (p: ProjectSummary) => void;
}) {
  const completed = projects.filter((p) => p.status === "completed" && p.result);
  const failed = projects.filter((p) => p.status === "failed");

  if (projects.length === 0) return null;

  const workloads = completed.reduce((n, p) => n + (p.result?.vm_count ?? 0), 0);
  // Sum only what was actually priced. An on-premises estate contributes 0
  // because its cost is not computable, not because it is free — folding it
  // in would understate the total while looking complete.
  const pricedEstates = completed.filter((p) => p.result?.priced !== false);
  const unpricedCount = completed.length - pricedEstates.length;
  const monthly = pricedEstates.reduce((n, p) => n + (p.result?.estimated_monthly_cost_usd ?? 0), 0);
  const measured = completed.reduce((n, p) => n + (p.result?.measured_sizing_count ?? 0), 0);
  const measuredPct = workloads ? Math.round((measured / workloads) * 100) : 0;

  const withConfidence = completed.filter((p) => p.result?.confidence);
  const avgConfidence = withConfidence.length
    ? withConfidence.reduce((n, p) => n + (p.result!.confidence!.overall ?? 0), 0) /
      withConfidence.length
    : null;

  const byCloud = completed.reduce<Record<string, { estates: number; workloads: number; monthly: number }>>(
    (acc, p) => {
      const k = p.target;
      acc[k] ??= { estates: 0, workloads: 0, monthly: 0 };
      acc[k].estates += 1;
      acc[k].workloads += p.result?.vm_count ?? 0;
      acc[k].monthly += p.result?.estimated_monthly_cost_usd ?? 0;
      return acc;
    },
    {},
  );

  // Low measured coverage is the single most important thing to say out loud:
  // it means the cost figure above rests on what somebody once allocated,
  // not on what the workloads use, and any saving derived from it is unproven.
  const sizingTone = measuredPct >= 80 ? "good" : measuredPct >= 30 ? "neutral" : "warn";
  const sizingNote =
    workloads === 0
      ? undefined
      : measuredPct >= 80
        ? "Most sizes rest on observed utilization."
        : measuredPct === 0
          ? "No utilization in any inventory — every size is an allocation-based estimate."
          : "Sizes without utilization are allocation-based estimates.";

  return (
    <section className="rounded-xl border border-neutral-200 p-5 dark:border-neutral-800">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Across your estates</h2>
        <span className="text-xs opacity-60">
          {completed.length} analysed
          {failed.length ? ` · ${failed.length} failed` : ""}
          {projects.length - completed.length - failed.length
            ? ` · ${projects.length - completed.length - failed.length} in progress`
            : ""}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Workloads" value={workloads.toLocaleString()} />
        <Stat
          label="Est. monthly cost on target"
          value={pricedEstates.length ? money.format(monthly) : "—"}
          note={
            unpricedCount
              ? `Across ${pricedEstates.length} priced estate${pricedEstates.length === 1 ? "" : "s"}. ${unpricedCount} on-premises estate${unpricedCount === 1 ? " is" : "s are"} not priced — see below.`
              : "List price, no committed-use discount. Compute, storage, licensing, load balancers."
          }
        />
        <Stat
          label="Sized from measurement"
          value={workloads ? `${measuredPct}%` : "—"}
          note={sizingNote}
          tone={workloads ? sizingTone : "neutral"}
        />
        <Stat
          label="Avg. confidence"
          value={avgConfidence !== null ? `${Math.round(avgConfidence * 100)}%` : "—"}
          note="Scored per workload on how much the inventory told us."
        />
      </div>

      {/* The absent number, stated rather than blank. */}
      <div className="mt-3 rounded-lg border border-dashed border-neutral-300 p-4 text-sm dark:border-neutral-700">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <span className="text-xs uppercase opacity-60">Potential savings</span>
          <span className="text-xs opacity-60">not computed</span>
        </div>
        <p className="mt-1 leading-snug opacity-80">
          Needs your <strong>current</strong> infrastructure spend — VMware licensing, datacenter,
          power, hardware amortisation. No inventory export contains it and this tool will not
          invent it. The target-cloud figure above is real; a saving against nothing is not.
        </p>
      </div>

      {Object.keys(byCloud).length > 1 ? (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[420px] text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left opacity-70 dark:border-neutral-800">
                <th className="py-2 pr-4 font-medium">Target</th>
                <th className="py-2 pr-4 font-medium">Estates</th>
                <th className="py-2 pr-4 font-medium">Workloads</th>
                <th className="py-2 font-medium">Est. monthly</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(byCloud)
                .sort((a, b) => b[1].monthly - a[1].monthly)
                .map(([cloud, row]) => (
                  <tr key={cloud} className="border-b border-neutral-100 dark:border-neutral-900">
                    <td className="py-2 pr-4">{CLOUD_LABEL[cloud as Target] ?? cloud}</td>
                    <td className="py-2 pr-4 tabular-nums">{row.estates}</td>
                    <td className="py-2 pr-4 tabular-nums">{row.workloads.toLocaleString()}</td>
                    <td className="py-2 tabular-nums">{money.format(row.monthly)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {completed.length > 0 ? (
        <div className="mt-4">
          <div className="mb-2 text-xs uppercase opacity-60">Drill in</div>
          <ul className="divide-y divide-neutral-100 dark:divide-neutral-900">
            {completed.slice(0, 8).map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onOpen(p)}
                  className="flex w-full items-center justify-between gap-4 py-2 text-left text-sm hover:opacity-80"
                >
                  <span className="truncate font-medium">{p.name}</span>
                  <span className="shrink-0 tabular-nums opacity-70">
                    {CLOUD_LABEL[p.target]} · {p.result?.vm_count ?? 0} workloads ·{" "}
                    {money.format(p.result?.estimated_monthly_cost_usd ?? 0)}/mo
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
