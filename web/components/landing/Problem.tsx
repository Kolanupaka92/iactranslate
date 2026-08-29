import { PROBLEMS } from "@/lib/site";

export default function Problem() {
  return (
    <section id="problem" className="border-b border-border-subtle">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">The problem</p>
        <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          Migration planning is still a manual craft.
        </h2>

        <div className="mt-14 grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle md:grid-cols-3">
          {PROBLEMS.map((problem) => (
            <div key={problem.title} className="bg-background p-8">
              <h3 className="text-base font-semibold">{problem.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-muted">{problem.body}</p>
            </div>
          ))}
        </div>

        <p className="mt-10 max-w-2xl text-base text-muted">
          So teams either pay consultants for months, or accept whichever cloud the
          tool was built to sell.
        </p>
      </div>
    </section>
  );
}
