import Link from "next/link";

import { PRICING, bookACallHref } from "@/lib/site";

/**
 * One published number, three conversations.
 *
 * The entry price is visible so a buyer can disqualify themselves before taking
 * a call — that respects their time and concentrates ours. The tiers above it
 * stay "talk to us", because publishing enterprise pricing costs negotiating
 * room on exactly the deals worth having.
 */
export default function Pricing() {
  return (
    <section id="pricing" className="border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">Pricing</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Priced per estate, not per seat.
        </h2>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted">
          The work scales with how large and how messy the estate is — not with how
          many people read the answer. A consultancy bills a project straight through
          to its client as a line item.
        </p>

        <div className="mt-14 grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle lg:grid-cols-4">
          {PRICING.map((tier) => (
            <div
              key={tier.name}
              className={`flex flex-col bg-background p-8 ${
                tier.lead ? "ring-2 ring-inset ring-accent" : ""
              }`}
            >
              <p
                className={`font-mono text-xs uppercase tracking-widest ${
                  tier.lead ? "text-accent" : "text-muted"
                }`}
              >
                {tier.name}
              </p>
              <p className="mt-4 text-3xl font-semibold tracking-tight">{tier.price}</p>
              <p className="mt-1 text-sm text-muted">{tier.unit}</p>

              <ul className="mt-6 flex-1 space-y-2.5 text-sm leading-relaxed text-muted">
                {tier.points.map((point) => (
                  <li key={point} className="flex gap-2">
                    <span aria-hidden className="mt-[0.45rem] h-1 w-1 shrink-0 rounded-full bg-accent" />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-8">
                {tier.href ? (
                  <Link
                    href={tier.href}
                    className="inline-block rounded-lg border border-border-subtle px-4 py-2 text-sm font-medium transition-colors hover:bg-surface"
                  >
                    {tier.cta}
                  </Link>
                ) : (
                  <a
                    href={bookACallHref}
                    className={`inline-block rounded-lg px-4 py-2 text-sm font-medium transition-opacity hover:opacity-90 ${
                      tier.lead
                        ? "bg-foreground text-background"
                        : "border border-border-subtle"
                    }`}
                  >
                    {tier.cta}
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>

        <p className="mt-8 max-w-2xl text-sm leading-relaxed text-muted">
          Migration tooling typically runs 6–10% of total migration cost. On a
          mid-market programme that is a budget line measured in six figures, which
          is the number these should be judged against — not against a per-server
          rate.
        </p>
      </div>
    </section>
  );
}
