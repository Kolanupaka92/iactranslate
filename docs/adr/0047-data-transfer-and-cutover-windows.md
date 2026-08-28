# 0047. How long the data actually takes to move

**Status:** Accepted
**Date:** 2026-08-27

## Context

The architecture review's sharpest product criticism was that migration is not
an inventory problem: the tool translated compute and network while databases,
storage and the *data itself* went untouched, and data is usually the majority of
real migration effort.

`replatform.py` already advises where a managed database service would help, and
deliberately stops short of performing a data migration. What was missing was
simpler and more universally needed: **how long does moving this estate take, and
does a wave fit the cutover weekend?** That is the number a migration lead plans
around, and discovering the answer during the weekend is the expensive way to
learn it.

Every input is already in the plan except one the inventory cannot know — the
bandwidth of the link between the datacenter and the cloud. That is asked for
rather than assumed.

## Decision

A `transfer.py` analysis engine: reads the immutable plan, changes nothing
(ADR 0007), and estimates elapsed transfer time for the estate and per migration
wave.

**Pessimistic on purpose**, for the same reason the cost model is (ADR 0039). An
optimistic estimate gets written into a cutover plan. Three haircuts apply:

* **WAN efficiency, 70%.** A "1 Gbps" link does not move 1 Gbps of payload; TCP
  overhead, latency and windowing put sustained throughput well below line rate
  on a long-haul path.
* **Link share, 50% by default.** Migration traffic shares the link with
  production. Assuming otherwise is how a migration takes down the business it
  was meant to move.
* **Allocated, not used, capacity.** RVTools reports provisioned disk. A 500 GiB
  disk holding 40 GiB transfers 40 GiB, so this overstates wherever used capacity
  is unknown — the honest direction, and stated in the output rather than hidden.

**Findings name their remedy.** A wave that exceeds its window says to split it,
pre-seed with replication, or widen the window. An estate past ~20 TiB is told
that offline appliances (Snowball, Data Box, Transfer Appliance) or continuous
replication with a short delta cutover are how estates that size actually move —
past a point, wire time approaches the shipping time of a disk.

Estimates are produced per **wave**, reusing the sequence the tool already plans
(ADR 0024). An estate total is interesting; *"wave 5 needs 93 hours and you have
48"* is the finding someone can act on.

## Consequences

On the realistic 25-VM estate (10.6 TiB) over a 200 Mbps site link with half
allocated to migration, **four of eight waves exceed a 48-hour window** — and
over a 10 Gbps direct connect, none do. That spread is exactly the planning input
the tool could not previously provide.

`iactranslate transfer` exposes it, with `--json` for pipelines.

Deliberately excluded, and stated in every output: the delta re-sync at cutover,
application quiesce time, and post-copy validation. The estimate also assumes a
single sequential stream — parallel transfers reduce elapsed time but not total
bytes, and contend for the same link, so modelling them properly needs a
concurrency input this does not yet take.

Still open on the wider gap: file shares and object storage are not inventoried
at all, database data volume is not distinguished from OS disk, and there is
still no data-migration execution. This measures the problem; it does not move
the bytes.
