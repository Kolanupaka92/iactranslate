import { PIPELINE_STAGES } from "@/lib/site";

/**
 * The section that answers "is this just an LLM writing Terraform?".
 *
 * It is the first question a competent engineer asks and the fastest way to
 * lose them, so it is answered explicitly rather than left to be inferred from
 * an architecture diagram.
 */
export default function HowItWorks() {
  return (
    <section id="how" className="border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">How it works</p>
        <h2 className="mt-4 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
          One deterministic pipeline. The same input always gives the same output.
        </h2>

        <ol className="mt-14 grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE_STAGES.map((stage, i) => (
            <li key={stage.name} className="bg-background p-6">
              <span className="font-mono text-xs text-muted">
                {String(i + 1).padStart(2, "0")}
              </span>
              <h3 className="mt-2 font-mono text-sm font-semibold text-accent">
                {stage.name}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted">{stage.note}</p>
            </li>
          ))}
        </ol>

        <div className="mt-12 grid gap-8 rounded-xl border border-border-subtle bg-surface p-8 md:grid-cols-2">
          <div>
            <h3 className="text-base font-semibold">No, an LLM does not write your Terraform.</h3>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              AI is optional, off by default, and confined to structured decisions —
              grouping workloads and proposing instance types. Templates emit the code.
              A validation layer re-checks every choice against the target&rsquo;s real
              catalog, so an invalid suggestion is rejected rather than rendered. The
              plan records which engine actually ran, not which one you asked for.
            </p>
          </div>
          <div>
            <h3 className="text-base font-semibold">Your inventory stays yours.</h3>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              The tool never connects to your environment — it reads a file you already
              export. It runs fully offline with no cloud credentials. If you do turn AI
              on, hostnames, cluster names and network labels are replaced with
              consistent pseudonyms first, and addresses, tags and resource IDs are
              dropped entirely.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
