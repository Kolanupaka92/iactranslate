"use client";

import type { ProjectSummary } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-500",
  failed: "bg-red-500/15 text-red-400",
  uploaded: "bg-amber-500/15 text-amber-500",
  created: "bg-neutral-500/15 text-neutral-400",
};

/**
 * Projects the signed-in user owns.
 *
 * These already persisted across restarts (ADR 0025/0029) and the API has
 * always been able to list them — but nothing in the UI ever called it, so
 * every visit started from an empty form and finished work was unreachable.
 */
export default function ProjectList({
  projects,
  currentId,
  onOpen,
  onNew,
}: {
  projects: ProjectSummary[];
  currentId: string | null;
  onOpen: (p: ProjectSummary) => void;
  onNew: () => void;
}) {
  return (
    <aside className="lg:sticky lg:top-8">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">
          Your projects
        </h2>
        <button
          type="button"
          onClick={onNew}
          className="rounded-md border border-neutral-300 px-2 py-1 text-xs font-medium hover:border-emerald-600 hover:text-emerald-600 dark:border-neutral-700 dark:hover:border-emerald-500 dark:hover:text-emerald-400"
        >
          + New
        </button>
      </div>

      {projects.length === 0 ? (
        <p className="rounded-lg border border-dashed border-neutral-300 px-3 py-4 text-xs opacity-60 dark:border-neutral-800">
          No projects yet. Create one to get started — it&apos;ll stay here so you
          can come back to it.
        </p>
      ) : (
        <ul className="space-y-1">
          {projects.map((p) => {
            const active = p.id === currentId;
            return (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onOpen(p)}
                  aria-current={active ? "true" : undefined}
                  className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
                    active
                      ? "border-emerald-600 bg-emerald-500/5"
                      : "border-transparent hover:border-neutral-300 dark:hover:border-neutral-700"
                  }`}
                >
                  <span className="block truncate text-sm font-medium">{p.name}</span>
                  <span className="mt-1 flex items-center gap-2 text-xs opacity-70">
                    <span className="uppercase">{p.target}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        STATUS_STYLE[p.status] ?? STATUS_STYLE.created
                      }`}
                    >
                      {p.status}
                    </span>
                  </span>
                  {p.result && (
                    <span className="mt-1 block text-xs opacity-60">
                      {p.result.vm_count} VMs · $
                      {p.result.estimated_monthly_cost_usd.toLocaleString(undefined, {
                        maximumFractionDigits: 0,
                      })}
                      /mo
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
