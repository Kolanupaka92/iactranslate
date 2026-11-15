# 0068. The database has no public address

**Status:** Accepted
**Date:** 2026-09-15

## Context

Cloud SQL was provisioned with a public IP and reached from Cloud Run through
the Cloud SQL Auth Proxy sidecar. That is Google's default path and it is
authenticated and encrypted, but the instance still answers on the internet.
Every control between an attacker and the data was a credential or a policy:
the `iacapp` password, `ENCRYPTED_ONLY`, the authorised-networks list. None
of those is a substitute for the port not being reachable. The audit item was
"the database must not have a public address", and a private-only instance is
the only configuration that satisfies it by construction.

## Decision

**Cloud SQL gets a private address on the project VPC through Private
Services Access, Cloud Run reaches it over Direct VPC egress, and the public
address is removed.** Concretely: a reserved range peered to
`servicenetworking.googleapis.com`; the instance patched onto the `default`
network; the Cloud Run service attached to that network with
`--vpc-egress=private-ranges-only` so only RFC 1918 traffic enters the VPC
and everything else keeps its normal path; the DSN secret rotated to the
private address with `sslmode=require`.

**The Auth Proxy sidecar is gone, and that was not optional.** The first
attempt kept the sidecar and only added VPC egress; the revision failed to
start (`SFEClient is nil` — the proxy could not reach the Cloud SQL control
plane under restricted egress). Cloud Run kept traffic on the previous
healthy revision, so nothing was down, and the failure said clearly which
design is coherent: with a private address, the application connects to it
directly, the way any client on the VPC would. The proxy's job was to bridge
the public internet; there is no longer a public internet to bridge.

**Staged, with the irreversible step last.** Private address added while the
public one stayed → new revision deployed and verified end-to-end (register,
login, account deletion — real writes, reads and a multi-table transaction —
through the production URL; zero errors in the revision's logs) → only then
is the public address removed. At every stage the previous configuration was
still serving.

## Consequences

- The instance is reachable only from inside the VPC. Administrative access
  (the account clean-up in this session used the Auth Proxy from a laptop)
  now requires an in-VPC path: a bastion or IAP-tunnelled VM, or the Auth
  Proxy with `--private-ip` from a machine on the network. That is the
  intended cost — the same door closed to attackers is closed to convenience.
- The DSN secret contains the private address. It is still a secret: it
  carries the password. The operations guide names it; nothing logs it.
- Direct VPC egress is a Cloud Run gen2 feature; the service was already
  on gen2. There is no connector to size or pay for.
- A pooled-connection layer (PgBouncer, or Cloud SQL's managed pooling) is
  the next step if connection counts ever matter; the private address makes
  it a drop-in.
