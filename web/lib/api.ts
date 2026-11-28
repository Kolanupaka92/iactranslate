/** Typed client for the IaCTranslate FastAPI backend. */

/**
 * `||`, not `??`: an env var that is *set but empty* is the failure this hit in
 * production. `??` only falls back on null/undefined, so an empty string became
 * the API root and every call went to the web app instead of the API.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Versioned API root. The backend also serves the unprefixed paths for
 * back-compat, but those return a `Deprecation` header — our own client should
 * not be the thing triggering it.
 *
 * In the browser this is the same-origin proxy (`/api/v1`, rewritten to the API
 * in `next.config.ts`) rather than the API's own hostname. The two are deployed
 * separately and are therefore different *sites*, and a `SameSite` cookie does
 * not cross that boundary — sign-in succeeded and every request after it came
 * back unauthenticated. Going through the proxy makes the request same-origin,
 * so the cookie is sent and no CORS relaxation is needed for ordinary traffic.
 *
 * Server-side rendering has no origin to be relative to, so it keeps the
 * absolute URL.
 */
export const API_BASE =
  typeof window === "undefined" ? `${API_URL}/v1` : "/api/v1";

export type Target = "aws" | "azure" | "gcp" | "oci" | "digitalocean" | "nutanix" | "proxmox";
export type Source = "auto" | "vmware" | "hyperv" | "kubernetes" | "cloud" | "generic";

export type Renderer =
  | "terraform"
  | "terraform-foreach"
  | "pulumi"
  | "cloudformation"
  | "bicep"
  | "cdk"
  | "kubernetes";

/** A target and what it can do, straight from the server. */
export interface TargetInfo {
  name: Target;
  capabilities: string[];
  /** IaC formats this cloud can be emitted as. Not a full cross product —
   *  CloudFormation is an AWS service, Bicep an Azure one. Read from the API
   *  rather than duplicated here, so the picker cannot disagree with the
   *  server about what will be accepted. */
  renderers: Renderer[];
}
export type Provider = "rule" | "anthropic";

export interface InstanceRow {
  vm: string;
  instance_type: string;
  tier: string;
  /** No size in the target's catalog holds it; `instance_type` is too small. */
  undersized?: boolean;
  /** A disk is larger than the target accepts; it was planned at the limit. */
  disk_too_large?: boolean;
}

export interface ConfidenceSummary {
  overall: number;
  level: "high" | "medium" | "low";
  factor_averages: Record<string, number>;
  low_confidence_count: number;
}

export interface RunResult {
  vm_count: number;
  estimated_monthly_cost_usd: number;
  /** False for an on-premises target, where the figure above is 0 and means
   *  "not computed", never "free". Render the absence, not the number. */
  priced?: boolean;
  pricing_basis?: string;
  pricing_source?: "static" | "live";
  right_sized_count?: number;
  undersized_count?: number;
  disk_too_large_count?: number;
  /** Workloads whose size rests on measured utilization rather than on what
   *  was allocated. The estate-level honesty signal: when this is low, any
   *  saving shown is unproven, and the UI says so. */
  measured_sizing_count?: number;
  confidence?: ConfidenceSummary;
  instances: InstanceRow[];
  provider_requested?: Provider;
  provider_used?: Provider;
}

export interface ProjectSummary {
  id: string;
  name: string;
  target: Target;
  region: string | null;
  provider?: Provider;
  renderer?: Renderer;
  status: "created" | "uploaded" | "completed" | "failed";
  error?: string;
  result?: RunResult;
  /** The organisation it belongs to, or null for a personal project. */
  org_id?: string | null;
  /** The caller's role on it (multi-tenant only). */
  role?: string | null;
  owner?: boolean;
}

export interface CloudScore {
  cloud: Target;
  total_monthly_cost_usd: number;
  annual_cost_usd: number;
  windows_vms: number;
  linux_vms: number;
  cost_score: number;
  fit_score: number;
  os_score: number;
  weighted_score: number;
  /** False when it can't take every workload: no image for the OS, or no size
   *  large enough. `reasons` says which. Its cost isn't comparable. */
  eligible?: boolean;
  reasons: string[];
}

/** The weights behind `weighted_score`. Shipped in the response so the ranking
 *  can be checked by hand — the reason to trust this over a vendor's own tool. */
export interface ScoringWeights {
  cost: number;
  fit: number;
  os: number;
}

export interface Recommendation {
  recommended: Target;
  summary: string;
  ranked: CloudScore[];
  decisiveness: "clear" | "moderate" | "close";
  margin: number;
  /** The cloud ranked #2 — what `margin` is measured against. */
  runner_up?: string | null;
  weights: ScoringWeights;
  /** True when the caller supplied weights; the ranking is then not the neutral one. */
  custom_weights?: boolean;
  notes: string[];
}

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface Finding {
  id: string;
  category: string;
  severity: Severity;
  title: string;
  detail: string;
  recommendation: string;
  affected: string[];
}

export interface ReadinessScore {
  score: number;
  band: "ready" | "minor-gaps" | "needs-work" | "blocked";
  rationale: string;
}

export interface Assessment {
  project_name: string;
  source_platform: string;
  total_workloads: number;
  powered_on: number;
  powered_off: number;
  total_vcpu: number;
  total_memory_gib: number;
  total_storage_gib: number;
  windows_workloads: number;
  linux_workloads: number;
  unknown_os_workloads: number;
  utilization_coverage_pct: number;
  readiness: ReadinessScore;
  findings: Finding[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    // `credentials: "include"` sends the session cookie cross-origin (the web
    // app and API are separate origins in dev). The API must therefore echo a
    // specific Origin in IACTRANSLATE_CORS_ORIGINS — "*" is rejected by the
    // browser whenever credentials are included.
    res = await fetch(`${API_BASE}${path}`, { credentials: "include", ...init });
  } catch {
    throw new ApiError(
      0,
      `Cannot reach the IaCTranslate API at ${API_URL}. Is the backend running?`,
    );
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : (body.detail?.message ?? JSON.stringify(body.detail));
      if (Array.isArray(body.detail?.issues)) {
        detail += ` — ${body.detail.issues.join("; ")}`;
      }
    } catch {
      /* non-JSON error body; keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function createProject(
  name: string,
  target: Target,
  source: Source = "auto",
  region?: string,
  provider: Provider = "rule",
  renderer: Renderer = "terraform",
  orgId: string | null = null,
): Promise<ProjectSummary> {
  return request("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      target,
      source,
      region: region || null,
      provider,
      renderer,
      org_id: orgId,
    }),
  });
}

/** Targets with their capabilities and valid IaC formats. */
/**
 * Delete the signed-in account and everything it owns. The password is
 * required — a stolen session can already read the estate; it must not also
 * be able to make the loss permanent. On success the session cookie is cleared
 * by the server, so callers should treat the user as signed out.
 */
export function deleteAccount(currentPassword: string): Promise<void> {
  return request("/auth/me", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password: currentPassword }),
  });
}

export function listTargets(): Promise<TargetInfo[]> {
  return request("/targets");
}

export function uploadFile(
  projectId: string,
  file: File,
): Promise<ProjectSummary> {
  const form = new FormData();
  form.append("file", file);
  return request(`/projects/${projectId}/upload`, {
    method: "POST",
    body: form,
  });
}

export function runProject(projectId: string): Promise<ProjectSummary> {
  return request(`/projects/${projectId}/run`, { method: "POST" });
}

/** Rank every cloud. With no `weights` the API uses its neutral defaults; with
 *  them, the ranking comes back flagged `custom_weights` (ADR 0071). */
export function recommendClouds(
  projectId: string,
  weights?: ScoringWeights,
): Promise<Recommendation> {
  return request(
    `/projects/${projectId}/recommend`,
    weights
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(weights),
        }
      : { method: "POST" },
  );
}

export function assessEstate(projectId: string): Promise<Assessment> {
  return request(`/projects/${projectId}/assess`, { method: "POST" });
}

export function deleteProject(projectId: string): Promise<void> {
  return request(`/projects/${projectId}`, { method: "DELETE" });
}

/** Plain link targets. These are opened as ordinary navigations (`<a href>`,
 *  a new tab), so the browser attaches the session cookie by itself — which is
 *  exactly why auth here is a cookie and not a bearer token: a navigation has
 *  no fetch call to hang an Authorization header on. */
export function downloadUrl(projectId: string): string {
  return `${API_BASE}/projects/${projectId}/download`;
}

export function reportUrl(projectId: string): string {
  return `${API_BASE}/projects/${projectId}/report`;
}

/** POST the report and return its HTML.
 *
 *  Goes through the same credentialed path as every other call. A bare
 *  `fetch()` here omits the session cookie and 401s in multi-tenant mode —
 *  which is exactly what the page used to do. */
export async function fetchReportHtml(projectId: string): Promise<string> {
  const res = await fetch(reportUrl(projectId), {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new ApiError(res.status, "Could not generate the report.");
  return res.text();
}

export interface Identity {
  authenticated: boolean;
  multi_tenant: boolean;
  id?: string;
  email?: string;
  /** The owner has proven they receive mail at `email`. */
  email_verified?: boolean;
  /** The server can email links. Without it, never claim one was sent. */
  verification_email?: boolean;
  /** Project creation is refused until the address is verified. */
  verification_required?: boolean;
}

export function verifyEmail(token: string): Promise<void> {
  return request("/auth/verify-email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
}

export function resendVerification(): Promise<{ status: string }> {
  return request("/auth/verify-email/resend", { method: "POST" });
}

/** Who the caller is. `multi_tenant: false` means the deployment runs
 *  single-tenant (no accounts), so the UI should not render a login screen. */
export function whoami(): Promise<Identity> {
  return request("/auth/me");
}

export function register(email: string, password: string): Promise<Identity> {
  return request("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export function login(email: string, password: string): Promise<Identity> {
  return request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

/** Always resolves the same way whether or not the account exists. */
export function forgotPassword(email: string): Promise<{ status: string }> {
  return request("/auth/forgot-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, password: string): Promise<void> {
  return request("/auth/reset-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
}

export function logout(): Promise<void> {
  return request("/auth/logout", { method: "POST" });
}

export function listProjects(): Promise<ProjectSummary[]> {
  return request("/projects");
}

// --- organisations (ADR 0080) ------------------------------------------------

export type OrgRole = "member" | "admin" | "owner";

export interface Org {
  id: string;
  name: string;
  created_at: number;
  /** The caller's role in it. */
  role: OrgRole;
}

export interface OrgMember {
  user_id: string;
  email: string | null;
  role: OrgRole;
  joined_at: number;
}

export interface OrgDetail extends Org {
  members: OrgMember[];
  projects: number;
}

export interface OrgInvitation {
  id: string;
  org_id: string;
  email: string;
  role: OrgRole;
  created_at: number;
  expires_at: number;
  org_name?: string;
  emailed?: boolean;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function listOrgs(): Promise<Org[]> {
  return request("/orgs");
}

export function createOrg(name: string): Promise<Org> {
  return request("/orgs", { method: "POST", ...json({ name }) });
}

export function getOrg(orgId: string): Promise<OrgDetail> {
  return request(`/orgs/${orgId}`);
}

export function deleteOrg(orgId: string): Promise<void> {
  return request(`/orgs/${orgId}`, { method: "DELETE" });
}

export function changeOrgRole(orgId: string, userId: string, role: OrgRole): Promise<unknown> {
  return request(`/orgs/${orgId}/members/${userId}`, { method: "PATCH", ...json({ role }) });
}

export function removeOrgMember(orgId: string, userId: string): Promise<unknown> {
  return request(`/orgs/${orgId}/members/${userId}`, { method: "DELETE" });
}

export function inviteToOrg(orgId: string, email: string, role: OrgRole): Promise<OrgInvitation> {
  return request(`/orgs/${orgId}/invitations`, { method: "POST", ...json({ email, role }) });
}

export function listOrgInvitations(orgId: string): Promise<OrgInvitation[]> {
  return request(`/orgs/${orgId}/invitations`);
}

export function withdrawInvitation(orgId: string, invitationId: string): Promise<void> {
  return request(`/orgs/${orgId}/invitations/${invitationId}`, { method: "DELETE" });
}

export function myInvitations(): Promise<OrgInvitation[]> {
  return request("/invitations");
}

export function acceptInvitation(invitationId: string): Promise<Org> {
  return request(`/invitations/${invitationId}/accept`, { method: "POST" });
}

export function declineInvitation(invitationId: string): Promise<void> {
  return request(`/invitations/${invitationId}/decline`, { method: "POST" });
}

export function acceptInvitationLink(token: string): Promise<Org> {
  return request("/invitations/accept", { method: "POST", ...json({ token }) });
}

export function moveProject(projectId: string, orgId: string | null): Promise<ProjectSummary> {
  return request(`/projects/${projectId}/org`, { method: "PUT", ...json({ org_id: orgId }) });
}

// --- sign-in methods (ADR 0081) -------------------------------------------------

export interface AuthMethods {
  password: boolean;
  saml: boolean;
  oidc: boolean;
  /** The OIDC provider's name for its button, e.g. "Google". */
  oidc_name?: string | null;
}

export function authMethods(): Promise<AuthMethods> {
  return request("/auth/methods");
}

/** Full-page navigations, not fetches: IdP sign-in is a browser flow. */
export const samlLoginUrl = `${API_BASE}/auth/saml/login`;
export const oidcLoginUrl = `${API_BASE}/auth/sso/login`;
