"use client";

import type { RunResult } from "@/lib/api";

export default function RunSummary({ result }: { result: RunResult }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
          <div className="text-xs uppercase opacity-60">VMs migrated</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">{result.vm_count}</div>
        </div>
        <div className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase opacity-60">Est. monthly cost</div>
            {result.pricing_source === "live" ? (
              <span className="rounded-full bg-emerald-600/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                live prices
              </span>
            ) : null}
          </div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            $
            {result.estimated_monthly_cost_usd.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </div>
        </div>
      </div>

      {result.right_sized_count ? (
        <p className="rounded-lg border border-emerald-600/30 bg-emerald-600/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-400">
          Right-sized {result.right_sized_count} of {result.vm_count} workloads to observed
          utilization — sized to actual usage, not raw allocation.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left opacity-70 dark:border-neutral-800">
              <th className="py-2 pr-4 font-medium">Source VM</th>
              <th className="py-2 pr-4 font-medium">Instance type</th>
              <th className="py-2 font-medium">Tier</th>
            </tr>
          </thead>
          <tbody>
            {result.instances.map((i) => (
              <tr key={i.vm} className="border-b border-neutral-100 dark:border-neutral-900">
                <td className="py-2 pr-4 font-mono text-xs">{i.vm}</td>
                <td className="py-2 pr-4 font-mono text-xs">{i.instance_type}</td>
                <td className="py-2 capitalize">{i.tier}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
