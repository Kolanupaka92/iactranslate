"use client";

import { useState } from "react";

import type { Recommendation, ScoringWeights, Target } from "@/lib/api";

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

const WEIGHT_KEYS = [
  { key: "cost", label: "Cost" },
  { key: "fit", label: "Sizing fit" },
  { key: "os", label: "OS affinity" },
] as const;

/**
 * Sliders for the three weights. The API scales whatever it receives to sum to
 * 1, so the share shown beside each slider is what will actually be applied —
 * showing the raw slider value would let "0.5 / 0.5 / 0.5" read as 50% each.
 */
function WeightsPanel({
  initial,
  custom,
  busy,
  open,
  onToggle,
  onApply,
}: {
  initial: ScoringWeights;
  custom: boolean;
  busy?: boolean;
  open: boolean;
  onToggle: (open: boolean) => void;
  onApply: (w: ScoringWeights | null) => void;
}) {
  const [w, setW] = useState<ScoringWeights>(initial);
  const total = w.cost + w.fit + w.os;
  const unchanged =
    w.cost === initial.cost && w.fit === initial.fit && w.os === initial.os;

  return (
    <details
      open={open}
      onToggle={(e) => onToggle(e.currentTarget.open)}
      className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
    >
      <summary className="cursor-pointer font-medium">Adjust weights</summary>
      <div className="mt-3 space-y-3">
        {WEIGHT_KEYS.map(({ key, label }) => (
          <label key={key} className="flex items-center gap-3">
            <span className="w-24 shrink-0">{label}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={w[key]}
              onChange={(e) => setW({ ...w, [key]: Number(e.target.value) })}
              className="w-full accent-emerald-600"
              aria-label={`${label} weight`}
            />
            <span className="w-12 shrink-0 text-right tabular-nums text-xs opacity-70">
              {total > 0 ? `${Math.round((w[key] / total) * 100)}%` : "—"}
            </span>
          </label>
        ))}
        {total <= 0 && (
          <p className="text-xs text-red-700 dark:text-red-400">
            At least one weight must be above zero.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || total <= 0 || unchanged}
            onClick={() => onApply(w)}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {busy ? "Re-ranking…" : "Re-rank with these weights"}
          </button>
          {custom && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onApply(null)}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
            >
              Reset to default weights
            </button>
          )}
        </div>
        <p className="text-xs opacity-60">
          Weights choose between clouds that can run the estate; a cloud that
          cannot host every workload stays at the bottom whatever the weighting.
        </p>
      </div>
    </details>
  );
}

export default function RecommendTable({
  rec,
  onUseCloud,
  onReweight,
  busy,
  reweighting,
}: {
  rec: Recommendation;
  onUseCloud: (t: Target) => void;
  /** Re-rank with these weights, or with the API's defaults when null. */
  onReweight?: (w: ScoringWeights | null) => void;
  busy?: boolean;
  reweighting?: boolean;
}) {
  // Held here rather than in the panel, which remounts on every re-rank to
  // re-seed its sliders: trying several weightings should not mean reopening
  // the panel each time.
  const [weightsOpen, setWeightsOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="opacity-70">Recommended:</span>
        <span className="font-semibold uppercase">{rec.recommended}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs font-medium ${
            DECISIVENESS_STYLE[rec.decisiveness] ?? ""
          }`}
        >
          {/* "margin 0.06" said nothing on its own — not what it measures,
              against whom, or on what scale. `capitalize` is scoped to the one
              word that needs it; on the whole span it title-cased the sentence
              into "Moderate Lead · 0.06 Ahead Of OCI". */}
          <span className="capitalize">{rec.decisiveness}</span> lead
          {rec.runner_up
            ? ` · ${rec.margin.toFixed(2)} ahead of ${rec.runner_up.toUpperCase()}`
            : ` · margin ${rec.margin.toFixed(2)}`}
        </span>
        {/* A ranking under someone's own priorities is not the neutral
            recommendation, and must not be mistaken for it in a screenshot. */}
        {rec.custom_weights && (
          <span className="rounded-full bg-sky-600/15 px-2 py-0.5 text-xs font-medium text-sky-700 dark:text-sky-400">
            Custom weights
          </span>
        )}
      </div>

      {/* The weighting is the first thing an architect asks about, and it was
          only visible by reading the source. Showing it makes the ranking
          reproducible by hand. */}
      <p className="text-xs opacity-60">
        Weighted score = cost × {rec.weights.cost.toFixed(2)} + fit ×{" "}
        {rec.weights.fit.toFixed(2)} + OS × {rec.weights.os.toFixed(2)}. All component scores are 0–1, higher is better.
        The same weights apply to every cloud.
      </p>

      {onReweight && (
        <WeightsPanel
          // Re-seed the sliders from whatever ranking is now on screen.
          key={`${rec.weights.cost}/${rec.weights.fit}/${rec.weights.os}`}
          initial={rec.weights}
          custom={!!rec.custom_weights}
          busy={reweighting}
          open={weightsOpen}
          onToggle={setWeightsOpen}
          onApply={onReweight}
        />
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left opacity-70 dark:border-neutral-800">
              <th className="py-2 pr-4 font-medium">Cloud</th>
              <th className="py-2 pr-4 font-medium">Score</th>
              <th className="py-2 pr-4 text-right font-medium">Est. cost</th>
              <th className="py-2 pr-4 font-medium">
                Cost <span className="opacity-60">×{rec.weights.cost.toFixed(2)}</span>
              </th>
              <th className="py-2 pr-4 font-medium">
                Fit <span className="opacity-60">×{rec.weights.fit.toFixed(2)}</span>
              </th>
              <th className="py-2 pr-4 font-medium">
                OS <span className="opacity-60">×{rec.weights.os.toFixed(2)}</span>
              </th>
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
                    <span className="block">
                      ${s.total_monthly_cost_usd.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      <span className="opacity-60">/mo</span>
                    </span>
                    <span className="block text-xs opacity-60">
                      ${s.annual_cost_usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}/yr
                    </span>
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
