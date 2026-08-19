/** Typed client for the IaCTranslate FastAPI backend. */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Target = "aws" | "azure" | "gcp" | "oci" | "digitalocean";
export type Source = "auto" | "vmware" | "hyperv" | "kubernetes" | "cloud" | "generic";
export type Provider = "rule" | "anthropic";

export interface InstanceRow {
  vm: string;
  instance_type: string;
  tier: string;
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
  pricing_source?: "static" | "live";
  right_sized_count?: number;
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
  status: "created" | "uploaded" | "completed" | "failed";
  error?: string;
  result?: RunResult;
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
  reasons: string[];
}

export interface Recommendation {
  recommended: Target;
  summary: string;
  ranked: CloudScore[];
  decisiveness: "clear" | "moderate" | "close";
  margin: number;
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
    res = await fetch(`${API_URL}${path}`, { credentials: "include", ...init });
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
): Promise<ProjectSummary> {
  return request("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, target, source, region: region || null, provider }),
  });
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

export function recommendClouds(projectId: string): Promise<Recommendation> {
  return request(`/projects/${projectId}/recommend`, { method: "POST" });
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
  return `${API_URL}/projects/${projectId}/download`;
}

export function reportUrl(projectId: string): string {
  return `${API_URL}/projects/${projectId}/report`;
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

export function logout(): Promise<void> {
  return request("/auth/logout", { method: "POST" });
}

export function listProjects(): Promise<ProjectSummary[]> {
  return request("/projects");
}
