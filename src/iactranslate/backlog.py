"""Backlog — the assessment and wave plan as work items to import into a tracker.

A migration team turns the assessment's findings and the wave plan into tickets
by hand: a remediation item per finding, an epic per wave, a story per machine.
This does it deterministically, as **import files**, not API calls. Nothing is
sent anywhere, no tracker credentials are needed, and the team reviews the file
before it becomes tickets.

Three formats, each matching what its tool documents for import:

  * **Jira** — one CSV. `Work item ID` + `Parent` carry the hierarchy, parents
    before children; labels are repeated `Label` columns.
  * **Azure DevOps** — CSVs with no ID column (new items), hierarchy by indented
    `Title 1` / `Title 2` columns, HTML descriptions, at most 1,000 work items per
    file (Microsoft's limit), so large estates are split at wave boundaries.
  * **ServiceNow** — an `rm_epic` and an `rm_story` CSV for the Agile tables,
    keyed by `correlation_id` so a re-import updates instead of duplicating.
    ServiceNow imports through an import set and transform map; the generated
    README says how.

Neither Jira's nor Azure DevOps' CSV import can create "blocks" links, so wave
dependencies are written into each wave's description instead of being dropped.

Read-only analysis (ADR 0007): consumes the plan, assessment and wave report.
"""
from __future__ import annotations

import csv
import html
import io
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .assessment.models import InfrastructureAssessment, Severity
from .models import MigrationPlan
from .waves import WaveReport

FORMATS = ("jira", "azure-devops", "servicenow")

#: Azure DevOps' per-file import limit.
ADO_MAX_ROWS = 1000

#: The requirement-level work item type in each Azure DevOps process.
ADO_PROCESSES = {"agile": "User Story", "scrum": "Product Backlog Item",
                 "cmmi": "Requirement", "basic": "Issue"}

#: How many affected machines a finding lists before summarising the rest.
_AFFECTED_SHOWN = 25

_JIRA_PRIORITY = {"critical": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
_NUMERIC_PRIORITY = {"critical": "1", "high": "2", "medium": "3", "low": "4"}


class BacklogItem(BaseModel):
    id: str
    parent: Optional[str] = None
    kind: str = Field(description="'epic' | 'task'")
    title: str
    description: List[str] = Field(default_factory=list, description="Paragraphs")
    priority: Optional[str] = Field(default=None, description="Finding severity, when it has one")
    labels: List[str] = Field(default_factory=list)


def build_backlog(
    plan: MigrationPlan,
    waves: WaveReport,
    assessment: Optional[InfrastructureAssessment] = None,
) -> List[BacklogItem]:
    """Epics and tasks, parents before their children, in a stable order."""
    items: List[BacklogItem] = []

    actionable = [f for f in (assessment.findings if assessment else []) if f.severity != Severity.INFO]
    if actionable:
        r = assessment.readiness
        items.append(BacklogItem(
            id="remediation", kind="epic", title="Pre-migration remediation",
            description=[f"Readiness {r.score}/100 ({r.band}): {r.rationale}",
                         "Resolve or accept each item before the waves that include its machines."],
            labels=["iactranslate", "remediation"],
        ))
        for f in actionable:
            shown = ", ".join(f.affected[:_AFFECTED_SHOWN])
            more = len(f.affected) - _AFFECTED_SHOWN
            description = [f.detail]
            if f.recommendation:
                description.append(f"Recommendation: {f.recommendation}")
            if f.affected:
                description.append(f"Affected workloads ({len(f.affected)}): {shown}"
                                   + (f" and {more} more" if more > 0 else ""))
            items.append(BacklogItem(
                id=f"finding:{f.id}", parent="remediation", kind="task", title=f.title,
                description=description, priority=f.severity.value,
                labels=["iactranslate", "remediation", f.category],
            ))

    compute = {c.vm_name: c for c in plan.compute}
    wave_title = {w.id: f"Wave {w.sequence}: {w.name}" for w in waves.waves}
    for w in sorted(waves.waves, key=lambda w: w.sequence):
        epic_id = f"wave:{w.id}"
        description = [
            f"{len(w.workloads)} workload(s), {w.environment.value} environment.",
            ("Starts after: " + "; ".join(wave_title.get(d, d) for d in w.depends_on))
            if w.depends_on else "No wave dependencies.",
            f"Rollback: {w.rollback_strategy}",
            f"Estimated downtime: {w.estimated_downtime_minutes} minutes.",
        ]
        items.append(BacklogItem(
            id=epic_id, kind="epic", title=wave_title[w.id], description=description,
            labels=["iactranslate", "migration-wave", w.environment.value],
        ))
        for vm_name in w.workloads:
            c = compute.get(vm_name)
            if c is None:
                continue
            disks = f"{c.root_volume_gib} GiB root" + (
                f" + {', '.join(f'{d} GiB' for d in c.extra_volumes_gib)} data" if c.extra_volumes_gib else "")
            description = [
                f"Target: {c.instance_type} on {plan.target} ({c.vcpu} vCPU, {c.memory_gib:g} GiB), "
                f"{c.subnet_tier.value} subnet, {c.tier.value} tier.",
                f"Disks: {disks}. Image: {c.image_key}"
                + (f" (source OS: {c.source_os})" if c.source_os else "") + ".",
            ]
            if c.os_family_changed:
                description.append("Warning: the target image is a different OS family from the source.")
            if w.validation_checks:
                description.append("Acceptance criteria: " + "; ".join(w.validation_checks))
            items.append(BacklogItem(
                id=f"vm:{vm_name}", parent=epic_id, kind="task", title=f"Migrate {vm_name}",
                description=description, labels=["iactranslate", "workload", c.tier.value],
            ))
    return items


# --------------------------------------------------------------------------- #
# Formats
# --------------------------------------------------------------------------- #


def _csv(header: List[str], rows: List[List[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def to_jira_csv(items: List[BacklogItem]) -> str:
    """Jira's CSV importer: numeric IDs, Parent by ID, repeated Label columns."""
    number = {item.id: str(i + 1) for i, item in enumerate(items)}
    width = max((len(i.labels) for i in items), default=0)
    header = ["Work item ID", "Parent", "Work type", "Summary", "Description", "Priority"] + ["Label"] * width
    rows = []
    for item in items:
        labels = [label.replace(" ", "-") for label in item.labels]  # Jira labels cannot contain spaces
        rows.append([
            number[item.id], number.get(item.parent, "") if item.parent else "",
            "Epic" if item.kind == "epic" else "Task", item.title,
            "\n\n".join(item.description), _JIRA_PRIORITY.get(item.priority or "", ""),
        ] + labels + [""] * (width - len(labels)))
    return _csv(header, rows)


def to_azure_devops_csvs(items: List[BacklogItem], process: str = "agile") -> Dict[str, str]:
    """Azure Boards import files, split to Microsoft's 1,000-item limit.

    Hierarchy is by title indentation, so a child must follow its epic in the
    same file. Files break at epic boundaries; an epic with more children than
    fit is split into "(part n/m)" epics, the only way to keep every child
    attached without IDs that do not exist until import.
    """
    if process not in ADO_PROCESSES:
        raise ValueError(f"unknown Azure DevOps process '{process}' (available: {', '.join(ADO_PROCESSES)})")
    child_type = ADO_PROCESSES[process]
    groups: List[List[BacklogItem]] = []
    for item in items:
        if item.kind == "epic":
            groups.append([item])
        else:
            groups[-1].append(item)

    blocks: List[List[BacklogItem]] = []
    per_epic = ADO_MAX_ROWS - 1
    for group in groups:
        epic, children = group[0], group[1:]
        parts = [children[i:i + per_epic] for i in range(0, len(children), per_epic)] or [[]]
        for n, part in enumerate(parts, 1):
            title = epic.title if len(parts) == 1 else f"{epic.title} (part {n}/{len(parts)})"
            blocks.append([epic.model_copy(update={"title": title})] + part)

    files: List[List[BacklogItem]] = [[]]
    for block in blocks:
        if len(files[-1]) + len(block) > ADO_MAX_ROWS:
            files.append([])
        files[-1].extend(block)

    header = ["Work Item Type", "Title 1", "Title 2", "Description", "Priority", "Tags"]
    out: Dict[str, str] = {}
    for n, chunk in enumerate(files, 1):
        rows = []
        for item in chunk:
            epic = item.kind == "epic"
            rows.append([
                "Epic" if epic else child_type,
                item.title if epic else "", "" if epic else item.title,
                "".join(f"<p>{html.escape(p)}</p>" for p in item.description),
                _NUMERIC_PRIORITY.get(item.priority or "", ""),
                "; ".join(item.labels),
            ])
        name = "azure-devops.csv" if len(files) == 1 else f"azure-devops-{n:02d}.csv"
        out[name] = _csv(header, rows)
    return out


def to_servicenow_csvs(items: List[BacklogItem]) -> Dict[str, str]:
    """Agile Development tables: epics, then stories pointing at their epic."""
    epics = [i for i in items if i.kind == "epic"]
    stories = [i for i in items if i.kind != "epic"]
    return {
        "servicenow-rm_epic.csv": _csv(
            ["correlation_id", "short_description", "description", "priority"],
            [[f"iactranslate:{e.id}", e.title, "\n\n".join(e.description), ""] for e in epics]),
        "servicenow-rm_story.csv": _csv(
            ["correlation_id", "epic_correlation_id", "short_description", "description", "priority"],
            [[f"iactranslate:{s.id}", f"iactranslate:{s.parent}", s.title, "\n\n".join(s.description),
              _NUMERIC_PRIORITY.get(s.priority or "", "")] for s in stories]),
    }


_README = """# Migration backlog

Generated by IaCTranslate from the assessment and the wave plan: a
"Pre-migration remediation" epic with one item per finding, and one epic per
migration wave with one item per workload. Review before importing; nothing here
has been sent to any tracker.

Wave dependencies are written into each wave's description ("Starts after:"),
because neither Jira's nor Azure DevOps' CSV import can create "blocks" links.

## Jira (`jira.csv`)

Use the CSV importer's external-system import (the bulk creator cannot map
parent/child). Map `Work item ID`, `Parent`, `Work type`, `Summary`,
`Description`, `Priority` and each `Label` column. Work types are Epic and Task.

## Azure DevOps (`azure-devops*.csv`)

Boards > Queries > Import work items, one file at a time (Azure DevOps imports
at most 1,000 items per file; larger estates are split at wave boundaries).
Children are indented under their epic with `Title 2`. The child type is
{child_type} (the {process} process); regenerate with `--ado-process` for
Scrum, CMMI or Basic. Imported items are unsaved until you choose Save items.

## ServiceNow (`servicenow-rm_epic.csv`, `servicenow-rm_story.csv`)

Import through System Import Sets > Load Data, epics first, then stories. In
each transform map, set `correlation_id` as the coalesce field so re-imports
update rather than duplicate. In the story map, set the target `epic` field
from a field-map script:

    answer = (function () {{
      var e = new GlideRecord('rm_epic');
      return e.get('correlation_id', source.u_epic_correlation_id) ? e.getUniqueValue() : '';
    }})();

Priority is 1 (critical) to 4 (low); blank keeps your default. Table and field
names are the Agile Development defaults: check them against your instance.
"""


def backlog_files(
    plan: MigrationPlan,
    waves: WaveReport,
    assessment: Optional[InfrastructureAssessment] = None,
    ado_process: str = "agile",
) -> Dict[str, str]:
    """Every format, plus a README, keyed by path under `backlog/`."""
    items = build_backlog(plan, waves, assessment)
    files = {"backlog/README.md": _README.format(child_type=ADO_PROCESSES[ado_process], process=ado_process),
             "backlog/jira.csv": to_jira_csv(items)}
    files.update({f"backlog/{k}": v for k, v in to_azure_devops_csvs(items, ado_process).items()})
    files.update({f"backlog/{k}": v for k, v in to_servicenow_csvs(items).items()})
    return files
