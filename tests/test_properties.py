"""Property-based tests — the stand-in for a real customer estate.

Pre-customer, every fixture in this repo was written by the same people who
wrote the code, so the tests only ever ask questions the authors already
thought of. That is exactly how a proven Terraform injection and an Azure
naming bug survived 380+ passing tests: both were found the moment the input
stopped looking like `prod-web-01` (ADR 0031).

Hypothesis attacks that blind spot directly. Instead of asserting a specific
output for a known input, each test states an **invariant that must hold for
every estate** and lets the generator hunt for a counter-example — including
inputs no one would think to write down: empty names, 400-character hostnames,
zero-CPU rows, duplicate machines, unicode, control characters.

An invariant here is a promise to a customer we do not have yet. If one of
these fails, the product is broken for some real estate somewhere.
"""
from __future__ import annotations

import re

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from iactranslate.normalize import normalize, sanitize_identifier

# Deliberately nastier than any tidy fixture: unicode, punctuation, code
# metacharacters, whitespace — the stuff real CMDB exports are full of.
messy_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # exclude lone surrogates
    min_size=0,
    max_size=80,
)

raw_record = st.fixed_dictionaries({
    "name": messy_text,
    "cpus": st.one_of(st.integers(min_value=-4, max_value=256), st.none(), messy_text),
    "memory_value": st.one_of(st.floats(min_value=0, max_value=1_048_576, allow_nan=False,
                                        allow_infinity=False), st.none()),
    "memory_unit": st.sampled_from(["GiB", "MiB", "GB", "MB", None]),
    "disk_value": st.one_of(st.floats(min_value=0, max_value=1_048_576, allow_nan=False,
                                      allow_infinity=False), st.none()),
    "disk_unit": st.sampled_from(["GiB", "MiB", "GB", "MB", None]),
    "os": st.one_of(messy_text, st.none()),
    "ip": st.one_of(messy_text, st.none()),
    "cluster": st.one_of(messy_text, st.none()),
    "powerstate": st.one_of(st.sampled_from(["poweredOn", "poweredOff", "suspended"]),
                            messy_text, st.none()),
})

SLOW = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# -- the sanitizer holds for *all* strings, not the eight I thought of --------


@given(messy_text)
@settings(max_examples=500, deadline=None)
def test_sanitizer_output_is_always_injection_free(raw):
    """No string anywhere can produce code-injection metacharacters."""
    cleaned = sanitize_identifier(raw)
    for dangerous in ("${", "%{", "$(", '"', "'", "\\", "`", "<", ">", "{", "}"):
        assert dangerous not in cleaned
    assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned)


@given(messy_text)
@settings(max_examples=500, deadline=None)
def test_sanitizer_is_idempotent(raw):
    """Sanitizing twice must equal sanitizing once.

    If it weren't, a value could change every time it passed through the
    pipeline, and re-running a migration would silently rename resources —
    which for Terraform means destroy-and-recreate.
    """
    once = sanitize_identifier(raw)
    assert sanitize_identifier(once) == once


@given(messy_text)
@settings(max_examples=500, deadline=None)
def test_sanitizer_never_returns_empty_or_whitespace(raw):
    cleaned = sanitize_identifier(raw)
    assert cleaned and cleaned.strip() == cleaned


# -- normalize survives anything an inventory can contain --------------------


@given(st.lists(raw_record, min_size=0, max_size=25))
@SLOW
def test_normalize_never_crashes_and_produces_valid_models(records):
    """Whatever the file contains, parsing must not raise."""
    vms = normalize(records)
    for vm in vms:
        assert vm.vm_name and vm.vm_name.strip()
        assert vm.cpu >= 1, "a workload must have at least one vCPU to size"
        assert vm.memory_gib >= 0
        assert vm.total_disk_gib >= 0


@given(st.lists(raw_record, min_size=1, max_size=25))
@SLOW
def test_normalize_deduplicates_by_name(records):
    """Two rows for one machine must collapse — a real export repeats VMs
    across sheets, and duplicates would become duplicate Terraform resources."""
    vms = normalize(records)
    names = [vm.vm_name for vm in vms]
    assert len(names) == len(set(names))


@given(st.lists(raw_record, min_size=1, max_size=25))
@SLOW
def test_normalize_is_deterministic(records):
    """Same input, same output — the product's core promise (ADR 0001)."""
    first = [vm.model_dump() for vm in normalize(records)]
    second = [vm.model_dump() for vm in normalize(records)]
    assert first == second


# -- the end-to-end promise --------------------------------------------------


@given(st.lists(raw_record, min_size=1, max_size=12))
@SLOW
def test_every_workload_becomes_exactly_one_instance(records):
    """No workload silently dropped, none duplicated.

    Losing a VM is the worst possible failure for a migration tool: the
    customer's machine simply never arrives in the new cloud, and nothing in
    the output says so.
    """
    from iactranslate.agents import build_migration_plan
    from iactranslate.targets import get_target

    vms = normalize(records)
    if not vms:
        return
    plan = build_migration_plan(vms, project_name="prop", target=get_target("aws"))

    assert plan.vm_count == len(vms)
    assert {c.vm_name for c in plan.compute} == {v.vm_name for v in vms}
    # Terraform resource labels must be unique, or the config won't parse.
    labels = [c.resource_name for c in plan.compute]
    assert len(labels) == len(set(labels)), "duplicate Terraform resource label"


@given(st.lists(raw_record, min_size=1, max_size=12))
@SLOW
def test_generated_terraform_is_never_injectable(records):
    """The ADR 0031 guarantee, generalized past the eight payloads I invented."""
    from iactranslate.agents import build_migration_plan
    from iactranslate.generator.renderer import build_files
    from iactranslate.targets import get_target

    vms = normalize(records)
    if not vms:
        return
    target = get_target("aws")
    files = build_files(build_migration_plan(vms, project_name="prop", target=target), target)

    for name, body in files.items():
        if not name.endswith(".tf"):
            continue
        assert "${file(" not in body, f"{name}: evaluable file() call"
        assert "%{" not in body, f"{name}: HCL directive"
        assert "$(" not in body, f"{name}: shell substitution"
        assert 'provisioner "' not in body, f"{name}: provisioner block"


@given(st.lists(raw_record, min_size=1, max_size=12))
@SLOW
def test_terraform_resource_labels_are_always_syntactically_valid(records):
    """Terraform identifiers must match [a-zA-Z_][a-zA-Z0-9_-]* — a label that
    doesn't is a config that will not parse, whatever the estate looked like."""
    from iactranslate.agents import build_migration_plan
    from iactranslate.targets import get_target

    vms = normalize(records)
    if not vms:
        return
    plan = build_migration_plan(vms, project_name="prop", target=get_target("aws"))
    for c in plan.compute:
        assert re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", c.resource_name), c.resource_name


@given(st.lists(raw_record, min_size=1, max_size=12))
@SLOW
def test_costs_are_never_negative_or_nan(records):
    """A negative or NaN estimate would be shown to a customer as a number."""
    import math

    from iactranslate.agents import build_migration_plan
    from iactranslate.targets import get_target

    vms = normalize(records)
    if not vms:
        return
    plan = build_migration_plan(vms, project_name="prop", target=get_target("aws"))
    total = plan.total_estimated_monthly_cost_usd
    assert total >= 0 and not math.isnan(total)
    for c in plan.compute:
        assert c.estimated_monthly_cost_usd >= 0
