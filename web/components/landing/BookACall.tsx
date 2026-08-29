import { bookACallHref } from "@/lib/site";

/**
 * The page has one job, and this is it.
 *
 * Deliberately not a signup form. The product is pre-design-partner: what is
 * needed now is conversations with people who run migrations, and a form that
 * drops someone into an empty console answers a question nobody asked yet.
 */
export default function BookACall() {
  return (
    <section className="border-b border-border-subtle bg-surface">
      <div className="mx-auto max-w-3xl px-6 py-24 text-center">
        <h2 className="text-3xl font-semibold tracking-tight sm:text-4xl">
          Tell us about the estate you have to move.
        </h2>
        <p className="mx-auto mt-6 max-w-xl text-base leading-relaxed text-muted">
          We are looking for a small number of teams with a real migration in front of
          them. Bring your inventory export and we will walk through what the tool says
          about it — the readiness score, the cloud comparison, and the workloads it
          thinks will break.
        </p>
        <div className="mt-10">
          <a
            href={bookACallHref}
            className="inline-block rounded-lg bg-foreground px-8 py-4 text-sm font-medium text-background transition-opacity hover:opacity-90"
          >
            Book a call
          </a>
        </div>
        <p className="mt-6 text-sm text-muted">
          No sales process. A working session with the person who built it.
        </p>
      </div>
    </section>
  );
}
