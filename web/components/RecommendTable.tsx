"use client";

import type { Recommendation, Target } from "@/lib/api";

const DECISIVENESS_STYLE: Record<string, string> = {
  clear: "bg-emerald-600/15 text-emerald-700 dark:text-emerald-400",
  moderate: "bg-amber-600/15 text-amber-700 dark:text-amber-400",
  close: "bg-red-600/15 text-red-700 dark:text-red-400",
};

function Meter({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex items-center gap-2" title={`${label}: ${value.toFixed(2)}`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div
          className="h-full rounded-full bg-emerald-600"
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </div>
      <span className="tabular-nums text-xs opacity-70">{value.toFixed(2)}</span>
    </div>
  );
}

export default function RecommendTable({
  rec,
  onUseCloud,
  busy,
}: {
  rec: Recommendation;
  onUseCloud: (t: Target) => void;
  busy?: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="opacity-70">Recommended:</span>
        <span className="font-semibold uppercase">{rec.recommended}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${
            DECISIVENESS_STYLE[rec.decisiveness] ?? ""
          }`}
        >
          {rec.decisiveness} lead · margin {rec.margin.toFixed(2)}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left opacity-70 dark:border-neutral-800">
              <th className="py-2 pr-4 font-medium">Cloud</th>
              <th className="py-2 pr-4 font-medium">Score</th>
              <th className="py-2 pr-4 text-right font-medium">Est. $/mo</th>
              <th className="py-2 pr-4 font-medium">Cost</th>
              <th className="py-2 pr-4 font-medium">Fit</th>
              <th className="py-2 pr-4 font-medium">OS</th>
              <th className="py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {rec.ranked.map((s) => {
              const winner = s.cloud === rec.recommended;
              return (
                <tr
                  key={s.cloud}
                  className="border-b border-neutral-100 dark:border-neutral-900"
                >
                  <td className="py-2.5 pr-4 font-semibold uppercase">
                    {s.cloud}
                    {winner && (
                      <span className="ml-2 rounded bg-emerald-600/15 px-1.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400">
                        Recommended
                      </span>
                    )}
                  </td>
                  <td className="py-2.5 pr-4 tabular-nums font-medium">
                    {s.weighted_score.toFixed(2)}
                  </td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">
                    ${s.total_monthly_cost_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </td>
                  <td className="py-2.5 pr-4"><Meter value={s.cost_score} label="Cost" /></td>
                  <td className="py-2.5 pr-4"><Meter value={s.fit_score} label="Fit" /></td>
                  <td className="py-2.5 pr-4"><Meter value={s.os_score} label="OS affinity" /></td>
                  <td className="py-2.5 text-right">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onUseCloud(s.cloud)}
                      className="rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
                    >
                      Use {s.cloud.toUpperCase()}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="space-y-2 text-sm">
        {rec.ranked.map((s) => (
          <details key={s.cloud} open={s.cloud === rec.recommended}>
            <summary className="cursor-pointer font-medium uppercase">
              {s.cloud} — why
            </summary>
            <ul className="ml-5 mt-1 list-disc space-y-0.5 opacity-80">
              {s.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </details>
        ))}
      </div>

      {rec.notes.length > 0 && (
        <ul className="ml-5 list-disc space-y-0.5 text-xs opacity-70">
          {rec.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}

      <p className="text-xs opacity-60">{rec.summary}</p>
    </div>
  );
}
