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
          <div className="text-xs uppercase opacity-60">Est. monthly cost</div>
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            $
            {result.estimated_monthly_cost_usd.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
          </div>
        </div>
      </div>

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
