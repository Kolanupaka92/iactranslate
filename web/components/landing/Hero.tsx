import Link from "next/link";

import { BUILD_FACTS, bookACallHref } from "@/lib/site";

/**
 * The headline sells the *decision*, not the file format.
 *
 * "Turn VMware exports into Terraform" describes the last stage of the pipeline
 * and invites comparison with a converter script. The expensive part of a
 * migration is not emitting HCL — it is knowing which cloud, at what cost, in
 * what order, and what breaks on cutover night.
 */
export default function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border-subtle">
      {/* Decorative only; hidden from assistive tech. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:linear-gradient(to_right,currentColor_1px,transparent_1px),linear-gradient(to_bottom,currentColor_1px,transparent_1px)] [background-size:56px_56px]"
      />

      <div className="relative mx-auto max-w-6xl px-6 py-24 sm:py-32">
        <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-border-subtle bg-surface px-3 py-1 font-mono text-xs text-muted">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" />
          Runs from a file export — no agents, no access to your environment
        </p>

        {/* Measure and steps chosen so the first clause stays on one line at
            every breakpoint. At max-w-3xl/text-6xl it broke after "migration",
            orphaning "costs" — which reads as a mistake in the largest type on
            the page. */}
        <h1 className="max-w-5xl text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl xl:text-6xl">
          Know what your migration costs
          <br />
          <span className="text-muted">before you commit to a cloud.</span>
        </h1>

        <p className="mt-8 max-w-2xl text-lg leading-relaxed text-muted">
          IaCTranslate reads the inventory export you already have, scores the estate for
          readiness, ranks every cloud on real cost and fit, sequences the cutover, and
          emits infrastructure code that the cloud providers&rsquo; own tooling validates.
          Every decision carries a reason you can audit.
        </p>

        <div className="mt-10 flex flex-wrap items-center gap-4">
          <a
            href={bookACallHref}
            className="rounded-lg bg-foreground px-6 py-3 text-sm font-medium text-background transition-opacity hover:opacity-90"
          >
            Book a call
          </a>
          <Link
            href="/console"
            className="rounded-lg border border-border-subtle px-6 py-3 text-sm font-medium transition-colors hover:bg-surface"
          >
            Open the console
          </Link>
        </div>

        <dl className="mt-16 grid max-w-3xl grid-cols-2 gap-x-8 gap-y-6 border-t border-border-subtle pt-10 sm:grid-cols-4">
          {[
            [`${BUILD_FACTS.sources}`, "inventory sources"],
            [`${BUILD_FACTS.clouds}`, "target clouds"],
            [`${BUILD_FACTS.iacFormats}`, "IaC formats"],
            ["0", "agents installed"],
          ].map(([value, label]) => (
            <div key={label}>
              <dt className="font-mono text-3xl font-semibold tracking-tight">{value}</dt>
              <dd className="mt-1 text-sm text-muted">{label}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
