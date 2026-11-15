# 0049. Version the API before it has clients

**Status:** Accepted
**Date:** 2026-08-28

## Context

Twenty-four endpoints served at unversioned paths — `/projects`, `/auth/login`,
`/jobs/{id}`. The architecture review flagged this as a one-day fix that "costs
nothing now", which is exactly why it kept not being done.

The asymmetry is the whole argument. Adding a prefix before clients exist is a
mechanical change with no migration. Adding it afterwards breaks every caller,
and the first breaking change to an unversioned API is the one that teaches
customers the API is not stable.

## Decision

Routes are declared on an `APIRouter` and mounted twice: under **`/v1`**, and at
the legacy unprefixed paths.

The legacy mount stays because removing it is itself a breaking change and
belongs in a major release. It marks itself: responses carry `Deprecation: true`,
a `Warning: 299` notice, and a `Link: </v1/…>; rel="successor-version"`. A client
can then discover the move without reading a changelog, and a deployment can
measure how much traffic still needs migrating before the mount is removed.

**`/health` and `/metrics` stay versionless.** Probes and scrapers point at fixed
paths; versioning them would break every deployment's liveness check and every
Prometheus config for no benefit. They are infrastructure, not API surface.

The web client now calls `/v1`. Shipping a deprecation warning that our own
front end triggers would be absurd.

## Consequences

`/v1` is excluded from nothing and the legacy mount is hidden from the OpenAPI
schema (`include_in_schema=False`), so generated clients and documentation
describe only the versioned surface while existing callers keep working.

A test derives the route list from the app itself rather than a hand-kept list,
so an endpoint added to only one mount fails the build. Another asserts that a
project created under `/v1` is visible from the legacy path — this is one
service behind two mounts, not two namespaces, and a future refactor that
accidentally split state would be caught.

Still open: no deprecation *date* is published for the legacy mount, and there is
no per-mount traffic metric yet, so "how much traffic still needs migrating" is
currently answerable only from logs.
