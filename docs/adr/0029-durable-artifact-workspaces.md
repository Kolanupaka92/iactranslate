# 0029 — Durable artifact workspaces

**Status:** Accepted

## Context

[ADR 0025](0025-persistent-store-and-bearer-auth.md) made project *metadata*
survive a restart, and was explicit that it did not do the same for the
generated *files*. Every project's workspace came from `tempfile.mkdtemp()`,
which puts it under the system temp directory.

That is fine for the CLI and local use, and it is wrong for a hosted
deployment. `/tmp` is periodically cleaned by the OS, is a RAM disk on some
platforms, and is discarded entirely when a container is recycled. The failure
mode is worse than plain data loss: the database keeps a perfectly valid row
pointing at `zip_path`, so the project still lists as `completed` and the
download 409s or 500s. [ADR 0027](0027-multi-tenancy-and-session-auth.md) raised
the stakes by putting multiple tenants on one node.

## Decision

`new_workspace()` honours `IACTRANSLATE_WORKSPACE_ROOT`. When set, workspaces
are allocated under that directory — intended to be a mounted volume — instead
of the system temp directory. When unset, behaviour is exactly as before, so
the CLI and existing single-user deployments are untouched.

Both paths go through `mkdtemp`, which keeps the two properties that matter:
the directory name is unique (no collision between concurrent projects) and it
is created `0700` (one tenant's workspace is not readable by another local
user). Doing this by hand with `mkdir` would have silently dropped both.

## Consequences

- With `IACTRANSLATE_STORE=sqlite` **and** `IACTRANSLATE_WORKSPACE_ROOT` set,
  metadata and artifacts survive together: verified by generating a project,
  killing the server, restarting it, and downloading the identical 22142-byte
  ZIP.
- **What that test does and does not prove.** It proves artifacts under a
  durable root survive a restart. It does *not* reproduce the original failure,
  because files in `/tmp` also survive a plain process restart on the same
  host — the real failure modes are container recycling and OS temp cleanup,
  neither of which a local restart simulates. The claim here is "files are on a
  volume you control", not "we reproduced the outage".
- **Still not solved: multi-node.** A local volume is not shared between
  replicas, so a download routed to a different replica than the one that
  generated it will still miss. Object storage (S3/GCS) is the real answer and
  is unchanged as the open item; this makes single-node durable, which is what
  is buildable and verifiable here.
- **Workspace cleanup becomes operationally real.** Eviction and delete already
  `rmtree` a project's workspace, so the normal lifecycle is handled. But
  directories under a durable root now outlive the process, so a wiped database
  or an unclean shutdown can leave orphans that nothing will reclaim — worth an
  operator's attention in a way a self-cleaning `/tmp` never was.
