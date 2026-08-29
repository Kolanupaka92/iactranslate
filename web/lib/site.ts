/**
 * Landing-page content, in one place.
 *
 * Every number here is either **measured in this repository** or **carries a
 * published source**. Nothing is estimated to sound better. That is a product
 * rule, not a style preference: the whole pitch is that this tool tells you the
 * truth about your estate, and a landing page that inflates its own numbers has
 * already contradicted it.
 *
 * When a claim changes, change it here — the previous copy drifted (the deck
 * still says 403 tests and 32 ADRs, both long stale) precisely because the
 * numbers lived inline in the markup.
 */

/**
 * Where "book a call" goes.
 *
 * Swap this for a scheduling link (Calendly/Cal.com) when one exists — a
 * mailto: converts worse than a calendar, and this page's only job is to start
 * conversations.
 */
export const CONTACT_EMAIL = "ksk.dev87@gmail.com";

export const CALL_SUBJECT = "IaCTranslate — migration conversation";

export const bookACallHref =
  `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(CALL_SUBJECT)}`;

export const DOCS_URL = "https://iactranslate-docs.vercel.app";
export const OVERVIEW_URL = "https://iactranslate-docs.vercel.app/overview";
export const REPO_URL = "https://github.com/Kolanupaka92/iactranslate";

/** Measured in this repository on 2026-08-29. `pytest` and `.github/workflows/ci.yml`. */
export const BUILD_FACTS = {
  tests: 778,
  ciJobs: 10,
  adrs: 60,
  sources: 5,
  clouds: 5,
  iacFormats: 6,
} as const;

export const PROBLEMS = [
  {
    title: "It is done by hand, VM by VM",
    body:
      "Infrastructure engineers read a discovery export and hand-translate it into " +
      "infrastructure code, one workload at a time. It is slow, it is expensive, and " +
      "the reasoning behind each sizing decision lives in someone's head.",
  },
  {
    title: "No estate is only VMware",
    body:
      "Real estates are VMware plus Hyper-V plus physical plus a CMDB nobody trusts " +
      "plus a cloud footprint someone started in 2021. Tools that assume one source " +
      "cover a fraction of the problem.",
  },
  {
    title: "The vendor tools are not neutral",
    body:
      "A cloud provider's migration tooling is single-cloud, emits no portable " +
      "infrastructure code, and will never tell you a competitor is the better fit. " +
      "You get the answer the tool was built to sell.",
  },
] as const;

export const PIPELINE_STAGES = [
  { name: "parse", note: "Any inventory export — five sources, auto-detected" },
  { name: "normalize", note: "One canonical model, whatever the source" },
  { name: "assess", note: "Readiness score, risks, data-quality findings" },
  { name: "recommend", note: "Unbiased cloud ranking on cost, fit and OS" },
  { name: "plan", note: "Right-size, network, sequence into waves" },
  { name: "validate", note: "Catalog and schema checks before anything renders" },
  { name: "govern", note: "Policy engine blocks on your org's rules" },
  { name: "render", note: "Six IaC formats, from templates — never freehand" },
] as const;

/**
 * The comparison that matters. Framed as a structural argument rather than a
 * feature list, because the point is that these gaps are not oversights a
 * competitor could close next quarter — they conflict with the vendor's business.
 */
export const COMPARISON = {
  columns: ["IaCTranslate", "AWS", "Azure", "Google"],
  rows: [
    {
      capability: "Reads VMware, Hyper-V, Kubernetes, CMDB and cloud fleets",
      values: ["Yes", "Agents", "Agents", "Agents"],
    },
    {
      capability: "Emits portable infrastructure code you own",
      values: ["6 formats", "No", "No", "No"],
    },
    { capability: "Targets more than one cloud", values: ["5 clouds", "AWS only", "Azure only", "GCP only"] },
    { capability: "Will recommend a competitor when it wins", values: ["Yes", "Never", "Never", "Never"] },
    { capability: "Runs without touching your environment", values: ["Yes", "No", "No", "No"] },
  ],
} as const;

/**
 * The cost example. Every qualifier here is load-bearing and must survive any
 * edit: it is a 7-workload sample estate, at on-demand list price, with no
 * committed-use discount, and DigitalOcean is excluded for a stated reason.
 * Presenting the 31% without them would be exactly the fabricated-savings claim
 * the product exists to replace.
 */
export const COST_EXAMPLE = {
  swingPct: 31,
  rows: [
    { cloud: "OCI", monthly: 3864, recommended: true },
    { cloud: "GCP", monthly: 4320, recommended: false },
    { cloud: "Azure", monthly: 4919, recommended: false },
    { cloud: "AWS", monthly: 5051, recommended: false },
  ],
  caveat:
    "From a 7-workload sample estate. Full monthly cost — compute, block storage, " +
    "Windows licensing and load balancers — at on-demand list price, assuming no " +
    "committed-use discount. DigitalOcean prices lower and is excluded: it publishes " +
    "no Windows image, so it cannot host part of this estate, and a cloud that cannot " +
    "run the workload is not a cheaper option. Your estate will produce different " +
    "numbers; the point is that the spread is large and knowable before you commit.",
} as const;

/** Published third-party research. Sources are shown on the page, not just cited here. */
export const MARKET = [
  {
    stat: "86%",
    claim: "are actively reducing their VMware footprint",
    source: "CloudBolt, survey of 302 North American IT decision-makers, Feb 2026",
  },
  {
    stat: "4%",
    claim: "have actually finished migrating",
    source: "CloudBolt, survey of 302 North American IT decision-makers, Feb 2026",
  },
  {
    stat: "73%",
    claim: "run hybrid cloud today",
    source: "Flexera 2026 State of the Cloud Report, n=753",
  },
] as const;

export const AUDIENCE = [
  {
    role: "Cloud consultancies and MSPs",
    pain:
      "You bid migrations fixed-price and the assessment phase eats the margin. " +
      "This does the estate analysis in minutes and leaves you the client-ready report.",
  },
  {
    role: "Enterprise platform teams",
    pain:
      "You have to justify a target cloud to people who control the budget. " +
      "This gives you a costed comparison with the assumptions visible.",
  },
  {
    role: "Migration leads",
    pain:
      "You own a cutover date. This finds the dependencies, sequences the waves, " +
      "and flags the workloads that will fail on the night.",
  },
] as const;
