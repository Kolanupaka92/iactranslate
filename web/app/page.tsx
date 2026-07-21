"use client";

import { useCallback, useState } from "react";

import RecommendTable from "@/components/RecommendTable";
import RunSummary from "@/components/RunSummary";
import SourcePicker from "@/components/SourcePicker";
import TargetPicker from "@/components/TargetPicker";
import UploadDropzone from "@/components/UploadDropzone";
import {
  ApiError,
  createProject,
  deleteProject,
  downloadUrl,
  recommendClouds,
  runProject,
  uploadFile,
  type ProjectSummary,
  type Recommendation,
  type RunResult,
  type Source,
  type Target,
} from "@/lib/api";

type Busy = "create" | "upload" | "recommend" | "switch" | "run" | null;

function Section({
  step,
  title,
  active,
  children,
}: {
  step: number;
  title: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-xl border border-neutral-200 p-5 transition-opacity dark:border-neutral-800 ${
        active ? "" : "pointer-events-none opacity-40"
      }`}
    >
      <h2 className="mb-4 flex items-center gap-2 font-semibold">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-neutral-900 text-xs text-white dark:bg-neutral-100 dark:text-neutral-900">
          {step}
        </span>
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function Home() {
  const [name, setName] = useState("");
  const [target, setTarget] = useState<Target>("aws");
  const [source, setSource] = useState<Source>("auto");
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState(false);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) =>
    setError(e instanceof ApiError ? e.message : "Something went wrong.");

  const handleCreate = useCallback(async () => {
    setBusy("create");
    setError(null);
    try {
      setProject(await createProject(name.trim(), target, source));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [name, target, source]);

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
        setResult(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [project],
  );

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
        const fresh = await createProject(project.name, t, source);
        await uploadFile(fresh.id, file);
        void deleteProject(project.id).catch(() => {});
        setProject(fresh);
        setTarget(t);
        setResult(null);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(null);
      }
    },
    [project, file, source],
  );

  const handleRun = useCallback(async () => {
    if (!project) return;
    setBusy("run");
    setError(null);
    try {
      const summary = await runProject(project.id);
      setProject(summary);
      setResult(summary.result ?? null);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }, [project]);

  const handleReset = useCallback(() => {
    if (project) void deleteProject(project.id).catch(() => {});
    setProject(null);
    setFile(null);
    setUploaded(false);
    setRec(null);
    setResult(null);
    setError(null);
    setName("");
  }, [project]);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-bold">IaCTranslate</h1>
        <p className="mt-1 text-sm opacity-70">
          Convert any infrastructure inventory — VMware, Hyper-V, a CMDB export,
          or an existing cloud fleet — into production-ready Terraform for AWS,
          Azure, or GCP, in minutes.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="mb-6 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
        >
          {error}
        </div>
      )}

      <div className="space-y-5">
        <Section step={1} title="Create a migration project" active={!project}>
          <div className="space-y-4">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Project name, e.g. acme-datacenter-migration"
              maxLength={128}
              className="w-full rounded-lg border border-neutral-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-emerald-600 dark:border-neutral-700"
            />
            <TargetPicker value={target} onChange={setTarget} />
            <SourcePicker value={source} onChange={setSource} />
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
          active={!!project && !result}
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
          title="Not sure which cloud? Compare all three (optional)"
          active={uploaded && !result}
        >
          <button
            type="button"
            onClick={handleRecommend}
            disabled={busy === "recommend"}
            className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 disabled:opacity-50 dark:border-neutral-700 dark:hover:border-neutral-500"
          >
            {busy === "recommend" ? "Comparing clouds…" : "Compare AWS · Azure · GCP"}
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

        <Section
          step={4}
          title={`Generate Terraform for ${project?.target.toUpperCase() ?? "your cloud"}`}
          active={uploaded && !result}
        >
          <button
            type="button"
            onClick={handleRun}
            disabled={busy === "run"}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy === "run" ? "Generating…" : "Generate Terraform project"}
          </button>
        </Section>

        {result && project && (
          <Section step={5} title="Your Terraform project is ready" active>
            <RunSummary result={result} />
            <div className="mt-5 flex items-center gap-3">
              <a
                href={downloadUrl(project.id)}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-500"
              >
                Download ZIP
              </a>
              <button
                type="button"
                onClick={handleReset}
                className="rounded-lg border border-neutral-300 px-4 py-2 text-sm font-semibold hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-500"
              >
                Start over
              </button>
            </div>
            <p className="mt-3 text-xs opacity-60">
              Fill in the image-ID placeholders in terraform.tfvars, then run
              terraform init / plan / apply. A migration summary is included under
              documentation/.
            </p>
          </Section>
        )}
      </div>

      <footer className="mt-10 text-center text-xs opacity-50">
        Deterministic pipeline — AI makes structured decisions only; templates
        write the Terraform. Your inventory never leaves the server on the
        default provider.
      </footer>
    </main>
  );
}
