/** Typed client for the IaCTranslate FastAPI backend. */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Target = "aws" | "azure" | "gcp";

export interface InstanceRow {
  vm: string;
  instance_type: string;
  tier: string;
}

export interface RunResult {
  vm_count: number;
  estimated_monthly_cost_usd: number;
  instances: InstanceRow[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  target: Target;
  region: string | null;
  status: "created" | "uploaded" | "completed" | "failed";
  error?: string;
  result?: RunResult;
}

export interface CloudScore {
  cloud: Target;
  total_monthly_cost_usd: number;
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
    res = await fetch(`${API_URL}${path}`, init);
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
  region?: string,
): Promise<ProjectSummary> {
  return request("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, target, region: region || null }),
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

export function deleteProject(projectId: string): Promise<void> {
  return request(`/projects/${projectId}`, { method: "DELETE" });
}

export function downloadUrl(projectId: string): string {
  return `${API_URL}/projects/${projectId}/download`;
}
