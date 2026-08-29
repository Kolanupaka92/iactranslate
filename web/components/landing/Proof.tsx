import { BUILD_FACTS, COST_EXAMPLE, MARKET } from "@/lib/site";

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/**
 * The cost chart is the most persuasive thing on the page and therefore the
 * easiest to turn into a lie. The caveat is rendered at full size directly
 * beneath it — not as a footnote, not behind a tooltip. A prospect who only
 * reads the big number must still see that it is a 7-workload sample at list
 * price. Making assumptions visible is the product's core claim; burying them
 * here would undercut it on the first page they read.
 */
export default function Proof() {
  const max = Math.max(...COST_EXAMPLE.rows.map((r) => r.monthly));

  return (
    <section id="proof" className="border-b border-border-subtle bg-surface">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">
          Proof, not slideware
        </p>
        <h2 className="mt-4 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Same estate, four clouds, {COST_EXAMPLE.swingPct}% apart.
        </h2>

        <div className="mt-12 rounded-xl border border-border-subtle bg-background p-8">
          {/* Stacks on mobile. Kept as one flex row at every width, the label and
              price consumed 224 of 261px and left the bar 37px — at which point
              the gap between $3,864 and $5,051 is about nine pixels and the chart
              conveys nothing. The spread *is* the argument, so it gets the width. */}
          <ul className="space-y-6 sm:space-y-5">
            {COST_EXAMPLE.rows.map((row) => (
              <li
                key={row.cloud}
                className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4"
              >
                <div className="flex items-baseline justify-between gap-4 sm:contents">
                  <span className="font-mono text-sm sm:w-16 sm:shrink-0">{row.cloud}</span>
                  <span className="order-last font-mono text-sm sm:order-none sm:w-32 sm:shrink-0 sm:text-right">
                    {money.format(row.monthly)}
                    <span className="text-muted">/mo</span>
                  </span>
                </div>
                <span className="relative h-8 w-full overflow-hidden rounded bg-surface sm:flex-1">
                  <span
                    className={`absolute inset-y-0 left-0 rounded ${
                      row.recommended ? "bg-accent" : "bg-foreground/20"
                    }`}
                    style={{ width: `${(row.monthly / max) * 100}%` }}
                  />
                </span>
                <span className="w-28 shrink-0 text-xs text-accent max-sm:hidden">
                  {row.recommended ? "recommended" : ""}
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-8 border-t border-border-subtle pt-6 text-sm leading-relaxed text-muted">
            {COST_EXAMPLE.caveat}
          </p>
        </div>

        <div className="mt-12 grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle sm:grid-cols-2 lg:grid-cols-4">
          {[
            [BUILD_FACTS.tests.toLocaleString(), "automated tests"],
            [`${BUILD_FACTS.ciJobs}`, "CI jobs, green"],
            ["tofu ✓", "real provider validation"],
            [`${BUILD_FACTS.adrs}`, "architecture decision records"],
          ].map(([value, label]) => (
            <div key={label} className="bg-background p-6">
              <p className="font-mono text-2xl font-semibold tracking-tight">{value}</p>
              <p className="mt-1 text-sm text-muted">{label}</p>
            </div>
          ))}
        </div>

        <p className="mt-6 max-w-2xl text-sm leading-relaxed text-muted">
          Generated infrastructure code passes real <code className="font-mono">tofu validate</code>{" "}
          against the actual AWS, azurerm and Google providers — enforced continuously in
          CI, not demonstrated once.
        </p>

        <div className="mt-20 border-t border-border-subtle pt-12">
          <h3 className="text-xl font-semibold tracking-tight">Why this is happening now</h3>
          <div className="mt-8 grid gap-8 sm:grid-cols-3">
            {MARKET.map((item) => (
              <div key={item.claim}>
                <p className="font-mono text-4xl font-semibold tracking-tight">{item.stat}</p>
                <p className="mt-2 text-sm text-foreground">{item.claim}</p>
                <p className="mt-3 text-xs leading-relaxed text-muted">{item.source}</p>
              </div>
            ))}
          </div>
          <p className="mt-8 max-w-2xl text-sm leading-relaxed text-muted">
            Almost everyone has started. Almost nobody has finished. That gap is measured
            in estates that still need planning, costing and translating.
          </p>
        </div>
      </div>
    </section>
  );
}
