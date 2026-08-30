"use client";

import type { Renderer } from "@/lib/api";

/**
 * Which IaC format to generate.
 *
 * The engine has shipped six renderers for some time, but the console could
 * only ever produce Terraform — the API took no renderer at all, so five of the
 * six were reachable from the CLI and nowhere else.
 *
 * The valid set depends on the chosen cloud and is **not** a full cross
 * product: CloudFormation is an AWS service, Bicep an Azure one. That list
 * comes from `/targets` rather than being repeated here, so this cannot offer
 * something the server will refuse.
 */
export const RENDERER_LABELS: Record<Renderer, { label: string; blurb: string }> = {
  terraform: { label: "Terraform", blurb: "HCL · works on every cloud" },
  pulumi: { label: "Pulumi", blurb: "Python program" },
  cloudformation: { label: "CloudFormation", blurb: "JSON template" },
  bicep: { label: "Bicep", blurb: "Azure-native" },
  cdk: { label: "AWS CDK", blurb: "Python constructs" },
  kubernetes: { label: "Kubernetes", blurb: "KubeVirt manifests" },
};

export default function RendererPicker({
  value,
  available,
  onChange,
  disabled,
}: {
  value: Renderer;
  /** Valid formats for the selected cloud, from the API. */
  available: Renderer[];
  onChange: (r: Renderer) => void;
  disabled?: boolean;
}) {
  // While the target list is loading there is nothing trustworthy to offer, and
  // guessing would mean showing options the server may reject.
  if (available.length === 0) return null;

  return (
    <div>
      <label className="mb-2 block text-sm font-medium" id="renderer-label">
        Output format
      </label>
      <div
        className="grid grid-cols-2 gap-3 sm:grid-cols-3"
        role="radiogroup"
        aria-labelledby="renderer-label"
      >
        {available.map((id) => {
          const meta = RENDERER_LABELS[id];
          const selected = id === value;
          return (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              onClick={() => onChange(id)}
              className={`rounded-lg border px-4 py-3 text-left transition-colors disabled:opacity-50 ${
                selected
                  ? "border-emerald-600 bg-emerald-600/10 ring-1 ring-emerald-600"
                  : "border-neutral-300 hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
              }`}
            >
              <div className="text-sm font-semibold">{meta.label}</div>
              <div className="mt-0.5 text-xs opacity-70">{meta.blurb}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
