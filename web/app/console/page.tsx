"use client";

import { useCallback, useEffect, useState } from "react";

import AIToggle from "@/components/AIToggle";
import AssessmentPanel from "@/components/AssessmentPanel";
import Dashboard from "@/components/Dashboard";
import ProjectList from "@/components/ProjectList";
import RecommendTable from "@/components/RecommendTable";
import RunSummary from "@/components/RunSummary";
import SignIn from "@/components/SignIn";
import SourcePicker from "@/components/SourcePicker";
import RendererPicker, { RENDERER_LABELS } from "@/components/RendererPicker";
import TargetPicker from "@/components/TargetPicker";
import UploadDropzone from "@/components/UploadDropzone";
import {
  ApiError,
  assessEstate,
  createProject,
  deleteProject,
  downloadUrl,
  fetchReportHtml,
  listProjects,
  listTargets,
  logout,
  recommendClouds,
  runProject,
  uploadFile,
  whoami,
  type Assessment,
  type Identity,
  type Provider,
  type ProjectSummary,
  type Recommendation,
  type Renderer,
  type RunResult,
  type Source,
  type Target,
  type TargetInfo,
} from "@/lib/api";

/**
 * Keep a chosen format only if the new cloud actually supports it.
 *
 * Terraform is the safe fallback because it is the one renderer valid for every
 * target; silently keeping `bicep` while switching to AWS would send a request
 * the API rejects.
 */
function rendererFor(
  target: Target,
  current: Renderer,
  info: TargetInfo[] = [],
): Renderer {
  const allowed = info.find((t) => t.name === target)?.renderers;
  if (!allowed || allowed.includes(current)) return current;
  return "terraform";
}

type Busy = "create" | "upload" | "assess" | "recommend" | "switch" | "run" | "report" | null;
type StepState = "pending" | "active" | "done";

/**
 * One step of the flow.
 *
 * A finished step collapses to a single summary row. Previously every step
 * stayed fully expanded forever: step 1 alone held 496px of a form nobody
 * would touch again, and reaching "Generate" meant scrolling ~1,900px past
 * completed work in a 720px viewport. Collapsing keeps the whole flow on one
 * screen while leaving finished work re-openable rather than hidden.
 */
function Section({
  step,
  title,
  state,
  summary,
  collapseWhenDone = true,
  children,
}: {
  step: number;
  title: string;
  state: StepState;
  summary?: string;
  /** Steps whose output is something to *read* (the assessment, the cloud
   *  comparison) must stay open when they complete. Collapsing them the moment
   *  the result arrives hides the very thing the user just asked for — which
   *  is exactly what the first version of this did. Only pure-input steps
   *  collapse on their own. */
  collapseWhenDone?: boolean;
  children: React.ReactNode;
}) {
  const [manuallyToggled, setManuallyToggled] = useState<boolean | null>(null);
  const open = manuallyToggled ?? !collapseWhenDone;
  const collapsed = state === "done" && !open;
  const setOpen = (next: boolean) => setManuallyToggled(next);

  return (
    <section
      className={`rounded-xl border transition-colors ${
        state === "active"
          ? "border-neutral-300 dark:border-neutral-700"
          : "border-neutral-200 dark:border-neutral-800"
      } ${state === "pending" ? "pointer-events-none opacity-40" : ""}`}
    >
      <div className={collapsed ? "" : "p-5"}>
        {collapsed ? (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="flex w-full items-center gap-3 px-5 py-3 text-left"
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-[11px] text-white">
              ✓
            </span>
            <span className="text-sm font-medium">{title}</span>
            {summary && (
              <span className="ml-auto truncate text-xs opacity-60">{summary}</span>
            )}
          </button>
        ) : (
          <>
            <h2 className="mb-4 flex items-center gap-2 font-semibold">
              <span
                className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                  state === "done"
                    ? "bg-emerald-600 text-white"
                    : "bg-neutral-900 text-white dark:bg-neutral-100 dark:text-neutral-900"
                }`}
              >
                {state === "done" ? "✓" : step}
              </span>
              {title}
              {state === "done" && (
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="ml-auto text-xs font-normal opacity-60 hover:opacity-100"
                >
                  Collapse
                </button>
              )}
            </h2>
            {children}
          </>
        )}
      </div>
    </section>
  );
}

export default function Home() {
  const [name, setName] = useState("");
  const [target, setTarget] = useState<Target>("aws");
  const [source, setSource] = useState<Source>("auto");
  const [provider, setProvider] = useState<Provider>("rule");
  const [renderer, setRenderer] = useState<Renderer>("terraform");
  // Which formats each cloud accepts, from the API. Empty until it loads, which
  // is why RendererPicker renders nothing rather than guessing.
  const [targetInfo, setTargetInfo] = useState<TargetInfo[]>([]);
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState(false);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  // null = still checking who we are; drives the sign-in gate below.
  const [identity, setIdentity] = useState<Identity | null>(null);

  // A 401 in multi-tenant mode simply means "not signed in yet".
  const fetchIdentity = () =>
    whoami().catch<Identity>(() => ({ authenticated: false, multi_tenant: true }));

  const refreshIdentity = useCallback(async () => {
    setIdentity(await fetchIdentity());
  }, []);

  // Loaded once. A failure leaves the list empty, which hides the format picker
  // rather than offering choices the server might refuse — the whole flow still
  // works and produces Terraform, which is what it did before this existed.
  useEffect(() => {
    let cancelled = false;
    void listTargets()
      .catch((): TargetInfo[] => [])
      .then((rows) => {
        if (!cancelled) setTargetInfo(rows);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshProjects = useCallback(async () => {
    // Never fatal — an empty sidebar is better than blocking the whole page.
    setProjects(await listProjects().catch(() => []));
  }, []);

  // Resolves in a promise callback, not synchronously in the effect body. The
  // `cancelled` guard drops a response that lands after unmount or re-run.
  useEffect(() => {
    let cancelled = false;
    void fetchIdentity().then((next) => {
      if (!cancelled) setIdentity(next);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Same shape as the identity bootstrap: resolve in a promise callback rather
  // than synchronously in the effect body, and drop a response that lands
  // after unmount.
  useEffect(() => {
    if (!identity) return;
    let cancelled = false;
    void listProjects()
      .catch(() => [])
      .then((next) => {
        if (!cancelled) setProjects(next);
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? e.message : "Something went wrong.");

  const handleCreate = useCallback(async () => {
    setBusy("create");
    setError(null);
    try {
      const created = await createProject(
        name.trim(), target, source, undefined, provider, renderer,
      );
      setProject(created);
      void refreshProjects();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [name, target, source, provider, renderer, refreshProjects]);

  const handleFile = useCallback(
    async (f: File) => {
      if (!project) return;
      setBusy("upload");
      setError(null);
      try {
        await uploadFile(project.id, f);
        setFile(f);
        setUploaded(true);
        setRec(null);
        setAssessment(null);
        setResult(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [project],
  );

  const handleAssess = useCallback(async () => {
    if (!project) return;
    setBusy("assess");
    setError(null);
    try {
      setAssessment(await assessEstate(project.id));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [project]);

  const handleRecommend = useCallback(async () => {
    if (!project) return;
    setBusy("recommend");
    setError(null);
    try {
      setRec(await recommendClouds(project.id));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [project]);

  const handleUseCloud = useCallback(
    async (t: Target) => {
      if (!project || !file) return;
      if (t === project.target) return;
      setBusy("switch");
      setError(null);
      try {
        // Re-running against a different cloud can invalidate the format —
        // CloudFormation does not exist on Azure — so fall back to Terraform,
        // which every target supports, rather than sending a request the API
        // will refuse.
        const nextRenderer = rendererFor(t, renderer, targetInfo);
        const fresh = await createProject(
          project.name, t, source, undefined, provider, nextRenderer,
        );
        await uploadFile(fresh.id, file);
        void deleteProject(project.id).catch(() => {});
        setProject(fresh);
        setTarget(t);
        setRenderer(nextRenderer);
        setResult(null);
        void refreshProjects();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [project, file, source, provider, renderer, targetInfo, refreshProjects],
  );

  const handleRun = useCallback(async () => {
    if (!project) return;
    setBusy("run");
    setError(null);
    try {
      const summary = await runProject(project.id);
      setProject(summary);
      setResult(summary.result ?? null);
      void refreshProjects();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [project, refreshProjects]);

  const handleViewReport = useCallback(async () => {
    if (!project) return;
    setBusy("report");
    setError(null);
    try {
      const html = await fetchReportHtml(project.id);
      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
      window.open(url, "_blank", "noopener");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [project]);

  /** Clear the workspace for a new project — without deleting the old one.
   *  "Start over" used to delete the current project outright, which is only
   *  safe when finished work is unreachable anyway. It isn't any more. */
  const handleNew = useCallback(() => {
    setProject(null);
    setFile(null);
    setUploaded(false);
    setRec(null);
    setAssessment(null);
    setResult(null);
    setError(null);
    setName("");
  }, []);

  /** Reopen an existing project from the sidebar. The uploaded inventory
   *  itself stays on the server, so the flow resumes from its status. */
  const handleOpenProject = useCallback((p: ProjectSummary) => {
    setProject(p);
    setTarget(p.target);
    setName(p.name);
    setFile(null);
    setUploaded(p.status !== "created");
    setResult(p.result ?? null);
    setRec(null);
    setAssessment(null);
    setError(null);
  }, []);

  if (identity === null) {
    return <main className="p-10 text-sm opacity-70">Loading…</main>;
  }

  if (identity.multi_tenant && !identity.authenticated) {
    return <SignIn onSignedIn={refreshIdentity} />;
  }

  const stepState = (done: boolean, active: boolean): StepState =>
    done ? "done" : active ? "active" : "pending";

  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">IaCTranslate</h1>
          {/* This read "Convert any infrastructure inventory ... into
              production-ready Terraform", which is the converter framing the
              product direction rules out, and it named Terraform four times
              while the engine ships six IaC formats. The translation is the
              last stage; the value is knowing which cloud and at what cost. */}
          <p className="mt-1 max-w-2xl text-sm opacity-70">
            Score an estate for migration readiness, compare every cloud on real
            cost and fit, sequence the cutover, and generate infrastructure code
            the providers&rsquo; own tooling validates — from an inventory export
            you already have.
          </p>
        </div>
        {identity.authenticated && (
          <p className="text-xs opacity-60">
            {identity.email}
            <button
              type="button"
              onClick={async () => {
                await logout();
                await refreshIdentity();
              }}
              className="ml-2 underline"
            >
              Sign out
            </button>
          </p>
        )}
      </header>

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
        >
          {error}
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
        <ProjectList
          projects={projects}
          currentId={project?.id ?? null}
          onOpen={handleOpenProject}
          onNew={handleNew}
        />

        <div className="space-y-4">
          {/* The landing view: what a returning user has across every estate,
              before they pick one. Hidden once a project is open so it does not
              compete with that project's own result card. */}
          {!project && <Dashboard projects={projects} onOpen={handleOpenProject} />}

          {/* The payoff goes first once it exists. Burying it under five
              completed steps meant scrolling past finished work to reach it. */}
          {result && project && (
            <section className="rounded-xl border border-emerald-600/40 bg-emerald-500/5 p-5">
              <h2 className="mb-4 font-semibold">
                Your {RENDERER_LABELS[project?.renderer ?? "terraform"].label} project is ready
              </h2>
              <RunSummary result={result} />
              <div className="mt-5 flex flex-wrap items-center gap-3">
                <a
                  href={downloadUrl(project.id)}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
                >
                  Download ZIP
                </a>
                <button
                  type="button"
                  onClick={handleViewReport}
                  disabled={busy === "report"}
                  className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
                >
                  {busy === "report" ? "Building report…" : "View executive report"}
                </button>
                <button
                  type="button"
                  onClick={handleNew}
                  className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
                >
                  New project
                </button>
              </div>
              <p className="mt-3 text-xs opacity-60">
                OS images resolve automatically — add cloud credentials (and a GCP
                project ID) and run terraform init / plan / apply. A migration
                summary and readiness assessment are included under documentation/.
              </p>
            </section>
          )}

          <Section
            step={1}
            title="Create a migration project"
            state={stepState(!!project, !project)}
            summary={project ? `${project.name} · ${project.target.toUpperCase()}` : undefined}
          >
            <div className="space-y-4">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Project name, e.g. acme-datacenter-migration"
                maxLength={128}
                className="w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-emerald-600 dark:border-neutral-700"
              />
              <TargetPicker
                value={target}
                onChange={(t) => {
                  setTarget(t);
                  // Switching cloud can invalidate the format, so reconcile it
                  // here rather than letting the picker show a selection that
                  // is no longer in its own list.
                  setRenderer((r) => rendererFor(t, r, targetInfo));
                }}
              />
              <RendererPicker
                value={renderer}
                available={
                  targetInfo.find((t) => t.name === target)?.renderers ?? []
                }
                onChange={setRenderer}
              />
              <SourcePicker value={source} onChange={setSource} />
              <AIToggle value={provider} onChange={setProvider} />
              <button
                type="button"
                onClick={handleCreate}
                disabled={!name.trim() || busy === "create"}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy === "create" ? "Creating…" : "Create project"}
              </button>
            </div>
          </Section>

          <Section
            step={2}
            title="Upload your discovery export"
            state={stepState(uploaded, !!project && !uploaded)}
            summary={file?.name ?? (uploaded ? "inventory uploaded" : undefined)}
          >
            <UploadDropzone
              onFile={handleFile}
              disabled={!project || busy === "upload"}
              fileName={uploaded ? (file?.name ?? null) : null}
            />
            {busy === "upload" && <p className="mt-2 text-sm opacity-70">Uploading…</p>}
            <button
              type="button"
              disabled={!project || busy === "upload"}
              onClick={async () => {
                const blob = await (await fetch("/rvtools_sample.xlsx")).blob();
                void handleFile(new File([blob], "rvtools_sample.xlsx"));
              }}
              className="mt-3 text-sm text-emerald-600 underline-offset-2 hover:underline disabled:opacity-50 dark:text-emerald-400"
            >
              No export handy? Try the sample inventory (7 VMs)
            </button>
          </Section>

          <Section
            step={3}
            title="Assess migration readiness (optional)"
            state={stepState(!!assessment, uploaded && !result)}
            summary={
              assessment ? `Readiness ${assessment.readiness.score}/100` : undefined
            }
            collapseWhenDone={false}
          >
            <button
              type="button"
              onClick={handleAssess}
              disabled={busy === "assess"}
              className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
            >
              {busy === "assess" ? "Assessing…" : "Assess this estate"}
            </button>
            <p className="mt-2 text-xs opacity-60">
              Surfaces migration risks, cost-optimization opportunities, and data
              gaps, with a readiness score. Deterministic — no AI.
            </p>
            {assessment && (
              <div className="mt-4">
                <AssessmentPanel a={assessment} />
              </div>
            )}
          </Section>

          <Section
            step={4}
            title="Not sure which cloud? Compare them all (optional)"
            state={stepState(!!rec, uploaded && !result)}
            summary={rec ? `Recommended: ${rec.recommended.toUpperCase()}` : undefined}
            collapseWhenDone={false}
          >
            <button
              type="button"
              onClick={handleRecommend}
              disabled={busy === "recommend"}
              className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
            >
              {busy === "recommend"
                ? "Comparing clouds…"
                : "Compare AWS · Azure · GCP · OCI · DigitalOcean"}
            </button>
            {busy === "switch" && (
              <p className="mt-2 text-sm opacity-70">Switching target cloud…</p>
            )}
            {rec && (
              <div className="mt-4">
                <RecommendTable rec={rec} onUseCloud={handleUseCloud} busy={busy === "switch"} />
              </div>
            )}
          </Section>

          {!result && (
            <Section
              step={5}
              title={`Generate ${RENDERER_LABELS[project?.renderer ?? renderer].label} for ${project?.target.toUpperCase() ?? "your cloud"}`}
              state={stepState(false, uploaded)}
            >
              <button
                type="button"
                onClick={handleRun}
                disabled={busy === "run"}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {busy === "run"
                  ? "Generating…"
                  : `Generate ${RENDERER_LABELS[project?.renderer ?? renderer].label} project`}
              </button>
            </Section>
          )}
        </div>
      </div>

      <footer className="mt-10 text-center text-xs opacity-50">
        Deterministic pipeline — AI makes structured decisions only; templates
        write the Terraform. Your inventory never leaves the server on the
        default provider.
      </footer>
    </main>
  );
}
