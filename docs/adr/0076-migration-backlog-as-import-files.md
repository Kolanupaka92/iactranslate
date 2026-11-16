# 0076. The migration backlog as import files, not tracker API calls

**Status:** Accepted
**Date:** 2026-11-16

## Context

Milestone v2.6: tickets from the assessment. A migration team turns the
assessment's findings and the wave plan into a backlog by hand, typically a
spreadsheet re-typed into Jira, Azure DevOps or ServiceNow.

The obvious build is API integrations: create the issues directly. That would
mean storing tracker credentials, sending customer inventory data to a third
party at runtime, and creating tickets nobody reviewed. All three cut against
how the product works: offline and deterministic, with output reviewed before
anything happens (the same reason GitOps pipelines are generated files).

## Decision

Generate **import files**, in each tool's documented format, into every package
(`backlog/`) and from `iactranslate backlog`:

- **A "Pre-migration remediation" epic**, one item per assessment finding with
  severity as priority. Informational findings, which need no action, are left
  out.
- **One epic per migration wave** (numbered as in `waves.json`), with its
  environment, rollback strategy, estimated downtime and the waves it depends
  on.
- **One item per workload**: source mapped to target, with the wave's
  validation checks as acceptance criteria.

Formats, from each vendor's import documentation:

- **Jira:** `Work item ID` + `Parent` for hierarchy, parents first; labels as
  repeated `Label` columns (Atlassian, "Prepare a CSV file for import").
- **Azure DevOps:** no `ID` column for new items, hierarchy by `Title 1` /
  `Title 2` indentation, no `State` or `Assigned To`, HTML descriptions, at most
  1,000 items per file (Microsoft, "Export, update, and import bulk work items
  with CSV files"). Files split at wave boundaries. A wave with more than 999
  workloads becomes "(part n/m)" epics, because a child must follow its parent
  in the same file and cannot reference an ID that doesn't exist until import.
  `--ado-process` picks the child type (User Story, Product Backlog Item,
  Requirement or Issue).
- **ServiceNow:** no standard hierarchical import exists; everything goes
  through an import set plus a transform map. `rm_epic` and `rm_story` CSVs
  carry `correlation_id` to coalesce on, so re-imports update instead of
  duplicating. The README gives the field-map script that resolves each story's
  epic, and says plainly to check table and field names against the instance.

Neither Jira's nor Azure DevOps' CSV import can create "blocks" links, so wave
dependencies are written into each wave's description instead of being dropped.

## Consequences

- No credentials, no runtime egress, and a reviewable file in the same bundle
  as the IaC.
- A spreadsheet can execute a cell that begins with `=`, `+`, `-` or `@`. Every
  cell starts with fixed text (a machine name only ever follows "Migrate "), and
  a test with formula-like machine names pins that.
- None of the three files has been imported into a live tracker here. The
  formats follow the vendor documentation and are pinned by tests, but the first
  import into each tool is the real check.
