"""Tracker import files from the assessment and the wave plan.

Each format is checked against what its tool documents for import: Jira's CSV
importer (Work item ID / Parent, repeated Label columns), Azure Boards' CSV
import (no ID for new items, Title 1/Title 2 indentation, 1,000 items per file)
and ServiceNow import sets (correlation_id to coalesce on).
"""
import csv
import io

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.assessment import assess
from iactranslate.backlog import (
    ADO_MAX_ROWS,
    BacklogItem,
    backlog_files,
    build_backlog,
    to_azure_devops_csvs,
    to_jira_csv,
    to_servicenow_csvs,
)
from iactranslate.cli import main
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target
from iactranslate.waves import plan_waves


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


@pytest.fixture(scope="module")
def estate():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    plan = build_migration_plan(vms, "backlog", get_target("aws"))
    return vms, plan, plan_waves(plan), assess(vms, "backlog", "vmware")


@pytest.fixture(scope="module")
def items(estate):
    _, plan, waves, assessment = estate
    return build_backlog(plan, waves, assessment)


# --- content ----------------------------------------------------------------------


def test_one_epic_per_wave_and_one_item_per_workload(estate, items):
    _, plan, waves, _ = estate
    wave_epics = [i for i in items if i.kind == "epic" and i.id.startswith("wave:")]
    assert len(wave_epics) == len(waves.waves)
    workload_items = [i for i in items if i.id.startswith("vm:")]
    assert sorted(i.title for i in workload_items) == sorted(f"Migrate {c.vm_name}" for c in plan.compute)


def test_remediation_covers_actionable_findings_only(estate, items):
    _, _, _, assessment = estate
    findings = [i for i in items if i.id.startswith("finding:")]
    actionable = [f for f in assessment.findings if f.severity.value != "info"]
    assert len(findings) == len(actionable)
    assert all(i.parent == "remediation" and i.priority for i in findings)


def test_parents_come_first_and_ids_are_unique(items):
    seen = set()
    for item in items:
        assert item.id not in seen
        if item.parent:
            assert item.parent in seen, f"{item.id} precedes its parent"
        seen.add(item.id)


def test_wave_dependencies_are_written_down_not_dropped(estate, items):
    _, _, waves, _ = estate
    dependent = next(w for w in waves.waves if w.depends_on)
    epic = next(i for i in items if i.id == f"wave:{dependent.id}")
    assert any(p.startswith("Starts after: ") for p in epic.description)


# --- Jira -----------------------------------------------------------------------------


def test_jira_csv_matches_the_importer(items):
    rows = _rows(to_jira_csv(items))
    header = rows[0]
    assert header[:6] == ["Work item ID", "Parent", "Work type", "Summary", "Description", "Priority"]
    assert set(header[6:]) == {"Label"}  # repeated column, one per label
    ids = set()
    for row in rows[1:]:
        assert row[0].isdigit() and row[0] not in ids
        if row[1]:
            assert row[1] in ids  # parent already defined above
        ids.add(row[0])
        assert row[2] in ("Epic", "Task")
        assert row[5] in ("", "Highest", "High", "Medium", "Low")
        assert all(" " not in label for label in row[6:])


# --- Azure DevOps -------------------------------------------------------------------------


def test_azure_devops_csv_matches_the_import(items):
    files = to_azure_devops_csvs(items)
    assert list(files) == ["azure-devops.csv"]
    rows = _rows(files["azure-devops.csv"])
    assert rows[0] == ["Work Item Type", "Title 1", "Title 2", "Description", "Priority", "Tags"]
    assert "ID" not in rows[0] and "State" not in rows[0]  # new items: no ID, no State
    assert rows[1][0] == "Epic"  # a child can only follow an epic
    for row in rows[1:]:
        if row[0] == "Epic":
            assert row[1] and not row[2]
        else:
            assert row[0] == "User Story" and row[2] and not row[1]
        assert row[3].startswith("<p>")


@pytest.mark.parametrize("process,child", [("scrum", "Product Backlog Item"),
                                           ("cmmi", "Requirement"), ("basic", "Issue")])
def test_azure_devops_child_type_follows_the_process(items, process, child):
    rows = _rows(to_azure_devops_csvs(items, process)["azure-devops.csv"])
    assert {r[0] for r in rows[1:]} == {"Epic", child}


def test_azure_devops_splits_at_the_1000_item_limit_without_orphans():
    epic = BacklogItem(id="wave:big", kind="epic", title="Wave 0: Production — app tier")
    children = [BacklogItem(id=f"vm:{n}", parent="wave:big", kind="task", title=f"Migrate vm-{n}")
                for n in range(2500)]
    small = [BacklogItem(id="wave:s", kind="epic", title="Wave 1: Dev"),
             BacklogItem(id="vm:s", parent="wave:s", kind="task", title="Migrate s")]
    files = to_azure_devops_csvs([epic] + children + small)
    total_children = 0
    for name, text in files.items():
        rows = _rows(text)[1:]
        assert len(rows) <= ADO_MAX_ROWS, name
        assert rows[0][0] == "Epic", f"{name} starts with an orphaned child"
        total_children += sum(1 for r in rows if r[0] != "Epic")
    assert total_children == 2501
    titles = [r[1] for text in files.values() for r in _rows(text)[1:] if r[0] == "Epic"]
    assert "Wave 0: Production — app tier (part 1/3)" in titles
    assert "Wave 0: Production — app tier (part 3/3)" in titles


def test_unknown_azure_devops_process_is_refused(items):
    with pytest.raises(ValueError, match="unknown Azure DevOps process"):
        to_azure_devops_csvs(items, "kanban")


# --- ServiceNow -----------------------------------------------------------------------------


def test_servicenow_stories_point_at_epics_by_correlation_id(items):
    files = to_servicenow_csvs(items)
    epics = _rows(files["servicenow-rm_epic.csv"])
    stories = _rows(files["servicenow-rm_story.csv"])
    assert epics[0][0] == stories[0][0] == "correlation_id"
    epic_ids = {r[0] for r in epics[1:]}
    assert all(r[1] in epic_ids for r in stories[1:])
    assert all(r[4] in ("", "1", "2", "3", "4") for r in stories[1:])
    ids = [r[0] for r in epics[1:] + stories[1:]]
    assert len(ids) == len(set(ids))  # coalescing on it must be unambiguous


# --- safety ---------------------------------------------------------------------------------


def test_no_cell_can_start_a_spreadsheet_formula():
    """People open these in Excel before importing. A machine named like a
    formula must never be the first character of a cell."""
    vms = normalize([{"name": "=HYPERLINK(1)", "cpus": 2, "memory_mib": 4096, "disks_mib": [20480]},
                     {"name": "+cmd", "cpus": 2, "memory_mib": 4096, "disks_mib": [20480]},
                     {"name": "@SUM(1)", "cpus": 2, "memory_mib": 4096, "disks_mib": [20480]}])
    plan = build_migration_plan(vms, "x", get_target("aws"))
    files = backlog_files(plan, plan_waves(plan), assess(vms, "x", "vmware"))
    for name, text in files.items():
        if not name.endswith(".csv"):
            continue
        for row in _rows(text):
            for cell in row:
                assert cell[:1] not in ("=", "+", "-", "@"), f"{name}: {cell[:30]!r}"


# --- surfaces ---------------------------------------------------------------------------------


def test_every_package_carries_the_backlog(tmp_path):
    out = tmp_path / "pkg"
    assert main(["translate", "tests/fixtures/rvtools_realistic.xlsx", "--target", "gcp",
                 "--out", str(out)]) == 0
    for name in ("README.md", "jira.csv", "azure-devops.csv",
                 "servicenow-rm_epic.csv", "servicenow-rm_story.csv"):
        assert (out / "backlog" / name).exists(), name


def test_cli_backlog_command(tmp_path, capsys):
    out = tmp_path / "bl"
    assert main(["backlog", "tests/fixtures/rvtools_realistic.xlsx", "--ado-process", "scrum",
                 "--out", str(out)]) == 0
    assert "Product Backlog Item" in (out / "azure-devops.csv").read_text()
    assert "Nothing was sent to any tracker" in capsys.readouterr().out
