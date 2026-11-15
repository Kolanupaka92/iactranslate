import { COMPARISON } from "@/lib/site";

/**
 * The strongest argument on the page, so it gets the most space.
 *
 * The gaps below are not features a competitor ships next quarter — they are
 * consequences of who is selling. A cloud vendor cannot recommend a rival and
 * cannot emit portable code that makes leaving easy. That is structural.
 */
export default function Difference() {
  return (
    <section id="difference" className="border-b border-border-subtle bg-surface">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <p className="font-mono text-xs uppercase tracking-widest text-accent">
          Why not the vendor&rsquo;s own tool
        </p>
        <h2 className="mt-4 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
          A cloud provider will never send you to a competitor — or tell you to stay put.
        </h2>
        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted">
          This is not a gap in their roadmap. It is a conflict with their business
          model, which is why it has stayed open for a decade. Most estates leaving
          VMware are moving to another hypervisor, not to a hyperscaler, and that is
          the one answer no cloud&rsquo;s migration tool can give.
        </p>

        {/* Scrolls inside its own container so the page body never scrolls sideways. */}
        <div className="mt-12 overflow-x-auto rounded-xl border border-border-subtle bg-background">
          <table className="w-full min-w-[42rem] text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle">
                <th scope="col" className="px-6 py-4 font-medium text-muted">
                  Capability
                </th>
                {COMPARISON.columns.map((column, i) => (
                  <th
                    key={column}
                    scope="col"
                    className={`px-6 py-4 font-medium ${i === 0 ? "" : "text-muted"}`}
                  >
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {COMPARISON.rows.map((row) => (
                <tr key={row.capability} className="border-b border-border-subtle last:border-0">
                  <th scope="row" className="px-6 py-4 font-normal">
                    {row.capability}
                  </th>
                  {row.values.map((value, i) => (
                    <td
                      key={`${row.capability}-${i}`}
                      className={`px-6 py-4 ${
                        i === 0 ? "font-medium text-accent" : "text-muted"
                      }`}
                    >
                      {value}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
