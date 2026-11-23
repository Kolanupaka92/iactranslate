# 0080. Organisations

**Status:** Accepted
**Date:** 2026-11-23

## Context

A project belonged to its creator and was shared one person at a time with
project roles (ADR 0050). That works for a consultant and a client. It fails for
a migration team:

- every new teammate needs a grant on every project;
- a project's visibility depends on whoever happened to create it;
- nothing is shared by default.

Separately, project grants were cached in each API process at startup. The
hosted API runs up to four instances, so a grant made on one was invisible on
the others, and a revoked grant kept working wherever it was still cached, until
a restart.

## Decision

**Organisations with three roles.**

- **member:** editor on every org project, and can create projects in the org.
- **admin:** admin on every org project, invites people and changes roles.
- **owner:** everything an admin can do, plus managing owners and deleting the
  org.

Only owners make or unmake owners, so an admin can't promote themselves past
the people who appointed them. An org always keeps at least one owner: the last
owner can't leave or step down, and can't delete their account while others
remain in the org.

**Access is the higher of two roles.** On an org project, a person holds the
higher of their project grant and what their org role gives. A grant can raise
access (an org member made approver on one project) but can't lower it, so an
org admin is admin everywhere in the org. The existing rule still applies: no
access is 404, insufficient access is 403.

**Joining is by invitation, consent and proof.** An admin invites an address.
The person joins only by accepting while signed in as that address:

- **From the emailed link.** Holding the token shows the mail arrived, so
  accepting also verifies the address.
- **In the app.** This needs an already verified address (ADR 0079). Otherwise
  whoever registered the invited address first could join in its owner's place.
  For the same reason, `GET /invitations` is empty for an unverified account,
  so a squatter can't learn which organisations invited the address.

Invitations are single-use, hashed, expire in 7 days, and a new one replaces
the old one. The invite endpoint is rate limited per inviter. Org names reject
control characters, because they appear in email subjects. The invitation and
`/invite` page work without a mailer: the console lists pending invitations.

**Projects.** A project has an optional `org_id`.

- It's created in an org by any member.
- Only its owner moves it in or out, because that changes who can see it.
- Deleting an org returns its projects to their owners rather than deleting
  them.
- Switching a project's cloud in the console keeps its org.

**No caching.** The org store and project grants read the database on every
check, which is a primary-key lookup. The in-memory backend runs the same SQL
on an in-memory SQLite database rather than a second implementation.

**Not built:** teams inside an organisation. An org is the team for now. Nested
groups add a resolution order (team role vs org role vs project grant) that is
worth designing only once a customer needs it.

## Consequences

- Tested in 25 API tests: joining by link, wrong-address and reused links,
  squatters neither seeing nor accepting invitations, in-app accept and decline,
  inviting rules, withdrawn invitations, org roles on projects for members and
  outsiders, grants raising but not lowering access, removal taking effect at
  once, moving projects (owner only, member orgs only), owner-only owner
  changes, last-owner protection, org deletion returning projects, account
  deletion waiting for an ownership handover, and a grant or revocation made
  through one instance seen at once by another on the same database.
- The console was checked against a local API: create an org, invite, create a
  project in the org; then, signed out, open the invite link, register as the
  invitee on that page, accept, and see the org's project.
- That check found a bug in the `/invite`, `/reset-password` and
  `/verify-email` pages: in development React runs effects twice, and the
  second run found the token already stripped from the URL. Production runs
  effects once, so it didn't show there. All three now read the token exactly
  once.
- A last-owner race remains: two owners demoting each other at the same moment
  could both pass the check. Acceptable at this scale, and a database
  constraint can close it later.
