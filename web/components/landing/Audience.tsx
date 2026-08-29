import { AUDIENCE } from "@/lib/site";

export default function Audience() {
  return (
    <section className="border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">Who it is for</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Built for the people who run migrations for a living.
        </h2>

        <div className="mt-14 grid gap-8 md:grid-cols-3">
          {AUDIENCE.map((item) => (
            <div key={item.role} className="rounded-xl border border-border-subtle p-8">
              <h3 className="text-base font-semibold">{item.role}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted">{item.pain}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
