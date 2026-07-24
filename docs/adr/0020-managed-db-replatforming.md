# 0020 — Managed-database re-platforming is advisory, not automated

**Status:** Accepted

## Context

Lift-and-shift puts a database on a plain VM. For a database tier, the
cloud-native move is often a *managed* service (Amazon RDS, Azure SQL, Cloud
SQL) that runs backups, patching, HA, and failover for you. Surfacing "these
database workloads are candidates for managed services" is real, useful
migration guidance.

The temptation is to *generate* the managed-database IaC. We deliberately do
not, and this ADR records why — because the boundary is the whole point.

## Decision

1. **Advisory only. The migration plan is never changed.** A database-tier
   workload still gets a lift-and-shift VM in the generated IaC. `replatform.py`
   produces a *report* (`replatforming.json` + a migration-summary section)
   naming the managed service each cloud offers for the detected engine, with
   suggested sizing and caveats — and stops there. A test asserts
   `analyze_replatforming` does not mutate the plan.
2. **Why not generate the managed DB?** Re-platforming a database is a
   *data-migration project*: schema conversion, replication/CDC cutover,
   connection-string changes, extension/feature-parity checks, downtime
   planning. IaCTranslate's stated scope (see architecture.md) explicitly
   excludes database schema/data migration. Emitting an `aws_db_instance` with
   an empty database would imply an automation we don't perform and can't make
   safe — worse than honestly flagging the opportunity.
3. **Engine detection is keyword-based and admits "unknown."** Engine is
   inferred from the workload name + hostname + OS
   (postgres/mysql/mariadb/sqlserver/oracle/mongodb/redis). When the name
   doesn't reveal it (`prod-db-01`), the report says so and adds a "confirm the
   actual database before choosing a managed service" caveat rather than
   guessing. Honest `unknown` beats a confident wrong mapping.
4. **The managed service is cloud-specific and admits gaps.** Each engine maps
   to that cloud's real managed offering; where a cloud has *no* fully-managed
   equivalent (GCP + Oracle, GCP + MongoDB), that's surfaced as a caveat naming
   the partner/self-managed path — not papered over with a fake service name.
5. **Cost is explicitly not compared head-to-head.** A managed instance bundles
   storage, backups, and HA a bare VM doesn't; the notes tell the reader to
   compare total cost of ownership, not sticker price, rather than printing a
   misleading "$X vs $Y" that ignores what's included.

## Consequences

- Adds genuinely useful re-platforming guidance while keeping the tool honest
  about its scope boundary — the generated infrastructure does exactly what it
  says, and the advice is clearly labeled advice.
- The report is deterministic (keyword matching + static service tables), so it
  is reviewable and defensible, consistent with the rest of the non-AI decision
  surface.
- **Honest limitation:** engine detection is only as good as the workload
  naming; sizing is a pass-through of the lift-and-shift instance's vCPU/mem
  (managed DB instance classes are memory-optimized and priced differently, so
  the suggested size is a starting point, not a quote). Both are stated in the
  caveats the report itself carries.
- If full re-platforming automation is ever in scope, it builds on this: the
  candidate list and engine detection are the inputs a generator would consume,
  but that's a separate, larger commitment with its own data-migration ADR.
