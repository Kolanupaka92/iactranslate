# 0021 — AI made reachable end-to-end, and always honestly labeled

**Status:** Accepted

## Context

`agents/providers/anthropic_provider.py` (Claude-powered classification and
instance sizing) has existed since the project's early scaffolding, selected
via `IACTRANSLATE_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`. But no entry
point ever set it: the CLI had no `--provider` flag, the API's project-create
body had no field for it, and the web UI had no toggle. The only way to use
it was an undocumented-to-the-user environment variable on the machine
running the process — invisible in the CLI's own `--help`, unusable per-request
from the API (env vars are process-wide, not per-call), and absent from the
web wizard entirely. Auditing this before adding anything new found the AI
capability was, in practice, dead.

Separately, every report the pipeline produces is deterministic prose driven
by templates (`exec_report.py`, `assessment/`) — genuinely useful, but a
"Summary" section written by hand-composed if/else clauses reads noticeably
more mechanical than the surrounding numbers deserve, and is exactly the kind
of short, low-stakes prose an LLM is well suited to improve *without*
influencing any decision.

## Decision

1. **`MigrationPlan.provider_used`** is a new field recording which engine
   *actually* classified and sized the plan — `"rule"` or `"anthropic"` — set
   once, by `agents/__init__.py::build_migration_plan`, from the resolved
   provider's own `.name`. This is the single source of truth `get_provider`'s
   existing silent-fallback behavior needed: requesting `anthropic` without a
   key still returns a working plan (by design), but nothing previously
   recorded whether that plan was actually AI-assisted or not.
2. **CLI**: `iactranslate translate --provider rule|anthropic` resolves and
   passes an explicit provider, overriding the environment default for that
   invocation. The summary line reports the *actual* engine used, with an
   explicit `[requested 'anthropic' but fell back — check ANTHROPIC_API_KEY]`
   note when they differ — never a silent "AI" claim that isn't true.
3. **API**: `POST /projects` gains a validated `provider` field (`"rule"` |
   `"anthropic"`, default `"rule"`), stored per-project and threaded into
   `run_pipeline`. This is the part that actually mattered: an env var can't
   let one API caller opt into AI while another doesn't, but a request field
   can. The run summary carries both `provider_requested` and
   `provider_used`, so a client can render the same honest fallback message
   the CLI does.
4. **Web UI**: an `AIToggle` checkbox in step 1 of the wizard ("Use AI
   (Claude) for classification & sizing"), off by default, with inline text
   naming the exact requirement (`ANTHROPIC_API_KEY` on the server) and the
   fallback behavior. `RunSummary` renders an amber fallback banner when
   `provider_requested !== provider_used`, and a green confirmation banner
   only when AI genuinely ran — verified live against a running API server
   with no key set, producing the fallback banner exactly as designed.
5. **`narrative.py`**: a new, small, strictly downstream module that produces
   the executive report's "Summary" paragraph. It calls Claude *only* when
   `plan.provider_used == "anthropic"` (i.e. only when the plan itself was
   genuinely AI-assisted — narrative generation piggybacks on that signal
   rather than making an independent, possibly-inconsistent choice to call
   the API). It is not a decision point: it cannot change the plan, the
   render, or any downstream artifact — it reads already-computed facts
   (assessment, confidence, cost) and writes prose, nothing else. On any
   failure (no key, network, empty response) it falls back to a deterministic
   templated paragraph built from the identical facts. The report always
   shows a badge naming which mode produced the text — "✨ AI-generated
   (Claude)" or "Deterministic summary" — so a paragraph is never mislabeled.

## Consequences

- The AI capability that already existed is now actually usable from every
  surface (CLI, API, web), not just importable Python.
- The honesty invariant established by `get_provider`'s silent fallback is now
  *visible* everywhere a caller might reasonably ask "did AI actually run?" —
  CLI stdout, API JSON, and the web UI — rather than requiring a caller to
  infer it from side effects (API latency, absence of an error).
- The narrative feature adds a second, independent AI touchpoint but keeps
  the "structured decisions only" boundary from `agents/base.py` intact:
  `LLMProvider.classify`/`.rightsize` still return typed Pydantic objects the
  validation layer re-checks; `narrative.generate_narrative` returns a string
  that only ever lands in a read-only report, never in the plan or any
  renderer's input.
- No new capability was added to the deterministic path — a user who never
  sets `ANTHROPIC_API_KEY` sees identical translate/report output before and
  after this change, just now with an honest "Deterministic summary" label
  instead of an unlabeled paragraph.
