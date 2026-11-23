"use client";

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  acceptInvitation,
  changeOrgRole,
  createOrg,
  declineInvitation,
  deleteOrg,
  getOrg,
  inviteToOrg,
  listOrgInvitations,
  myInvitations,
  removeOrgMember,
  withdrawInvitation,
  type Identity,
  type Org,
  type OrgDetail,
  type OrgInvitation,
  type OrgRole,
} from "@/lib/api";

const ROLES: OrgRole[] = ["member", "admin", "owner"];
const RANK: Record<OrgRole, number> = { member: 0, admin: 1, owner: 2 };
const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong.");

/**
 * Sidebar section: the caller's organisations, invitations waiting for them,
 * and creating a new org. Managing one opens a dialog (members, invitations,
 * roles), since the sidebar is too narrow for it.
 */
export default function OrgPanel({
  identity,
  orgs,
  onChanged,
}: {
  identity: Identity;
  orgs: Org[];
  onChanged: () => Promise<void>;
}) {
  const [invites, setInvites] = useState<OrgInvitation[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const loadInvites = useCallback(async () => {
    setInvites(await myInvitations().catch(() => []));
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadInvites();
  }, [loadInvites, orgs]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const org = await createOrg(name);
      setName("");
      setCreating(false);
      await onChanged();
      setOpen(org.id);
    } catch (err) {
      setError(message(err));
    }
  };

  const respond = async (inv: OrgInvitation, accept: boolean) => {
    setError(null);
    try {
      await (accept ? acceptInvitation(inv.id) : declineInvitation(inv.id));
      await loadInvites();
      await onChanged();
    } catch (err) {
      setError(message(err));
    }
  };

  return (
    <section>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wide opacity-60">Organisations</h2>
        <button
          type="button"
          onClick={() => setCreating((c) => !c)}
          className="rounded-md border border-neutral-300 px-2 py-1 text-xs font-medium hover:border-emerald-600 hover:text-emerald-600 dark:border-neutral-700"
        >
          + New
        </button>
      </div>

      {creating && (
        <form onSubmit={create} className="mb-3 flex flex-col gap-2">
          <input
            autoFocus
            required
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Organisation name"
            className="w-full rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 text-sm dark:border-neutral-700"
          />
          <button type="submit" className="rounded-md bg-neutral-900 px-2 py-1.5 text-xs font-medium text-white dark:bg-neutral-100 dark:text-neutral-900">
            Create
          </button>
        </form>
      )}

      {invites.length > 0 && (
        <ul className="mb-3 space-y-2">
          {invites.map((inv) => (
            <li key={inv.id} className="rounded-lg border border-emerald-600/40 bg-emerald-500/5 px-3 py-2 text-xs">
              <span className="block">
                Invited to <strong>{inv.org_name}</strong> as {inv.role}
              </span>
              <span className="mt-1 flex gap-3">
                <button type="button" onClick={() => respond(inv, true)} className="font-medium underline">
                  Accept
                </button>
                <button type="button" onClick={() => respond(inv, false)} className="underline opacity-70">
                  Decline
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {identity.email_verified === false && (
        <p className="mb-3 text-xs opacity-60">
          Confirm your email to see invitations sent to it here.
        </p>
      )}

      {orgs.length === 0 ? (
        <p className="text-xs opacity-60">
          Create one to share projects with a team, or accept an invitation.
        </p>
      ) : (
        <ul className="space-y-1">
          {orgs.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                onClick={() => setOpen(o.id)}
                className="w-full rounded-lg border border-transparent px-3 py-2 text-left hover:border-neutral-300 dark:hover:border-neutral-700"
              >
                <span className="block truncate text-sm font-medium">{o.name}</span>
                <span className="text-xs opacity-60">{o.role}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className="mt-2 text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      )}

      {open && (
        <OrgDialog
          orgId={open}
          me={identity.id ?? ""}
          onClose={() => setOpen(null)}
          onChanged={onChanged}
        />
      )}
    </section>
  );
}

function OrgDialog({
  orgId,
  me,
  onClose,
  onChanged,
}: {
  orgId: string;
  me: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [pending, setPending] = useState<OrgInvitation[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<OrgRole>("member");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const detail = await getOrg(orgId);
      setOrg(detail);
      setPending(RANK[detail.role] >= RANK.admin ? await listOrgInvitations(orgId) : []);
    } catch (err) {
      setError(message(err));
    }
  }, [orgId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = async (fn: () => Promise<unknown>, done?: string, close = false) => {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (done) setNotice(done);
      await onChanged();
      if (close) onClose();
      else await load();
    } catch (err) {
      setError(message(err));
    }
  };

  const isAdmin = org ? RANK[org.role] >= RANK.admin : false;
  const isOwner = org?.role === "owner";
  // The API refuses to let the last owner leave; don't offer it.
  const lastOwner = isOwner && (org?.members.filter((m) => m.role === "owner").length ?? 0) <= 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={org ? `Organisation ${org.name}` : "Organisation"}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 px-4 py-16"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-lg rounded-xl border border-neutral-200 bg-white p-6 text-sm shadow-xl dark:border-neutral-800 dark:bg-neutral-950">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold">{org?.name ?? "Loading…"}</h2>
            {org && (
              <p className="text-xs opacity-60">
                You&apos;re {org.role === "admin" ? "an" : "a"} {org.role} · {org.projects} project
                {org.projects === 1 ? "" : "s"}
              </p>
            )}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-lg leading-none opacity-60 hover:opacity-100">
            ×
          </button>
        </div>

        {org && (
          <>
            <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide opacity-60">Members</h3>
            <ul className="mt-2 divide-y divide-neutral-200 dark:divide-neutral-800">
              {org.members.map((m) => {
                const self = m.user_id === me;
                // Mirrors the API: only owners touch owners.
                const canEdit = !self && isAdmin && (isOwner || m.role !== "owner");
                return (
                  <li key={m.user_id} className="flex items-center justify-between gap-3 py-2">
                    <span className="truncate">
                      {m.email ?? m.user_id}
                      {self && <span className="opacity-60"> (you)</span>}
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {canEdit ? (
                        <select
                          aria-label={`Role for ${m.email ?? m.user_id}`}
                          value={m.role}
                          onChange={(e) =>
                            act(() => changeOrgRole(orgId, m.user_id, e.target.value as OrgRole))
                          }
                          className="rounded border border-neutral-300 bg-transparent px-1 py-0.5 text-xs dark:border-neutral-700"
                        >
                          {ROLES.filter((r) => isOwner || r !== "owner").map((r) => (
                            <option key={r} value={r}>
                              {r}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span className="text-xs opacity-70">{m.role}</span>
                      )}
                      {(canEdit || (self && !lastOwner)) && (
                        <button
                          type="button"
                          onClick={() =>
                            act(
                              () => removeOrgMember(orgId, m.user_id),
                              self ? undefined : "Removed.",
                              self,
                            )
                          }
                          className="text-xs underline opacity-70"
                        >
                          {self ? "Leave" : "Remove"}
                        </button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>

            {isAdmin && (
              <>
                <h3 className="mt-6 text-xs font-semibold uppercase tracking-wide opacity-60">Invite</h3>
                <form
                  className="mt-2 flex flex-wrap gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(async () => {
                      const inv = await inviteToOrg(orgId, email, role);
                      setEmail("");
                      setNotice(
                        inv.emailed
                          ? `Invitation emailed to ${inv.email}.`
                          : `Invitation created for ${inv.email}. This server can't send email, so they'll find it in the console after signing in with that address.`,
                      );
                    });
                  }}
                >
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="colleague@company.com"
                    className="min-w-0 flex-1 rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700"
                  />
                  <select
                    aria-label="Role for the invitation"
                    value={role}
                    onChange={(e) => setRole(e.target.value as OrgRole)}
                    className="rounded-lg border border-neutral-300 bg-transparent px-2 py-1.5 dark:border-neutral-700"
                  >
                    {ROLES.filter((r) => isOwner || r !== "owner").map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <button type="submit" className="rounded-lg bg-neutral-900 px-3 py-1.5 font-medium text-white dark:bg-neutral-100 dark:text-neutral-900">
                    Invite
                  </button>
                </form>
                <p className="mt-1 text-xs opacity-60">
                  Members can work on every project in the organisation; admins also manage
                  members and projects.
                </p>

                {pending.length > 0 && (
                  <ul className="mt-3 space-y-1 text-xs">
                    {pending.map((inv) => (
                      <li key={inv.id} className="flex items-center justify-between gap-2">
                        <span className="truncate">
                          {inv.email} · {inv.role} · pending
                        </span>
                        <button
                          type="button"
                          onClick={() => act(() => withdrawInvitation(orgId, inv.id))}
                          className="underline opacity-70"
                        >
                          Withdraw
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}

            {isOwner && (
              <div className="mt-8 border-t border-neutral-200 pt-4 dark:border-neutral-800">
                <button
                  type="button"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Delete ${org.name}? Its projects aren't deleted: each goes back to the person who created it.`,
                      )
                    ) {
                      void act(() => deleteOrg(orgId), undefined, true);
                    }
                  }}
                  className="text-xs text-red-700 underline dark:text-red-300"
                >
                  Delete organisation
                </button>
              </div>
            )}
          </>
        )}

        {notice && (
          <p role="status" className="mt-4 rounded bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
            {notice}
          </p>
        )}
        {error && (
          <p role="alert" className="mt-4 rounded bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/40 dark:text-red-300">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
