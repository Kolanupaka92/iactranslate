# 0055. Application dependencies from observed network flows

**Status:** Accepted
**Date:** 2026-08-28

## Context

The sharpest product criticism in the architecture review: **you cannot
correctly group applications from a static inventory.** Tiers are inferred from
names and ports, which misgroups real estates, and a migration wave built on a
wrong grouping breaks at cutover — the most expensive moment to find out.

I filed this as an eight-week effort needing agentless collectors, and that was
the third "blocked" item in a row that was not. Building a collector is eight
weeks. **Reading a flow export the customer already has is not** — every tool in
this space (vRealize Network Insight, Cisco Secure Workload, any NetFlow/sFlow
collector, even `ss` snapshots) exports the same shape: source, destination,
port, volume.

## Decision

An analysis engine that reads a flow export alongside the inventory and produces
three findings, in ascending order of how much trouble they save:

**Dependencies.** Direction comes from the *destination port*: the side being
connected to is the one that must already be running. That asymmetry is what
makes this a dependency rather than an association.

**Wave violations.** Checked against the waves the tool already plans, so the
output is *"wave 0 moves web-01 before db-01 in wave 5"* rather than a graph to
interpret.

**External dependencies.** Flows to addresses **not** in the inventory — a
mainframe, a licence server, a partner API. They do not migrate, so they must
stay reachable, and they are structurally invisible to any inventory-only
analysis. Private and public addresses are distinguished: a private one is an
internal system nobody listed, a public one is a third party.

**Infrastructure ports are filtered** — DNS, NTP, DHCP, SNMP, syslog, Kerberos.
Without this every workload "depends on" the DNS server and the graph says
nothing, which is the most common way a dependency map becomes useless. A single
connection is likewise ignored: one probe is noise, not architecture.

**It does not reclassify tiers.** A flow table can be incomplete — a short
capture window, a filtered span port — and quietly overriding a plan with partial
evidence would be worse than the heuristic it replaced. The findings are
presented for a human to act on.

## Consequences

`iactranslate depends --flows <csv>`, with `--json` for pipelines.

**On the fixture estate the wave planner is already correct** — zero violations —
and that is reported as such. An earlier synthetic fixture wired every web server
to every app server across dev, staging and production and produced eighteen
violations; that was fixture artifice, not a finding, and a demo built on it would
have been dishonest. The fixture generator now pairs within an environment,
because a dev web server does not call a production database.

Two defects surfaced while testing. The header normaliser replaced hyphens but
not spaces, so a `Destination Port` column — the form several tools actually
export — failed with "no recognisable dst_port column" on a file that plainly had
one. And a test asserted a substring the note did not contain, which was the test
being wrong rather than the code.

Still open, and this is the honest boundary: **this reads flows, it does not
collect them.** A customer without a flow tool gets nothing, and installing
collectors is the genuinely large piece. There is no time-window awareness, so a
capture taken during a quiet hour under-reports; no protocol beyond TCP/UDP port
numbers, so a shared database serving two applications looks like one dependency
cluster; and the findings do not feed back into wave planning automatically,
by the deliberate choice above.
