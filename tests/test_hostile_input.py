"""Adversarial and messy-data input (ADR 0031).

Two failures motivated this file, both found by feeding the pipeline something
other than the tidy fixture names (`prod-web-01`) every other test uses:

1. **Template injection.** A VM named `x-${file("/etc/passwd")}` was written
   verbatim into Terraform tags, and `tofu` *evaluated* it — the file's
   contents ended up in the value. No quote-breaking was needed, because HCL
   evaluates `${...}` inside string literals.
2. **Ordinary CMDB names broke Azure.** Names with spaces, parentheses, or
   dots — completely normal in real inventory — violate Azure's resource
   naming rules, so every VM in a realistic estate produced invalid Terraform.

The clean-name fixtures hid both. These tests exist so they stay fixed.
"""
from __future__ import annotations

import pytest

from iactranslate.normalize import normalize, sanitize_identifier

# Payloads aimed at the languages the renderers emit.
INJECTION_PAYLOADS = [
    'x-${file("/etc/passwd")}',            # HCL interpolation — evaluated by Terraform
    "y-%{ for x in [1] }boom%{ endfor }",  # HCL directive
    'evil", provisioner "local-exec" { command = "id" } #',  # HCL string break-out
    "a\\`id`",                             # shell substitution
    "b$(whoami)",                          # shell substitution
    "c<script>alert(1)</script>",          # HTML report
    "d'; DROP TABLE projects; --",         # quote handling
    "e\x00\x1fnull-and-control",           # control characters
]

# Not attacks — names a real CMDB export actually contains.
MESSY_REAL_NAMES = [
    "web server 01",
    "DB-Prod (Primary)",
    "app_srv#3",
    "svr.finance.local",
    "Exchange/MBX01",
    "ORACLE RAC NODE 1",
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_sanitizer_strips_code_injection_metacharacters(payload):
    cleaned = sanitize_identifier(payload)
    for dangerous in ('${', '%{', '$(', '"', "'", "\\", "`", "<", ">"):
        assert dangerous not in cleaned, f"{dangerous!r} survived in {cleaned!r}"
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned)


def test_sanitizer_leaves_legitimate_hostnames_untouched():
    """The fix must not mangle the names customers actually have."""
    for name in ("prod-web-01", "db.prod.internal", "app_server_3", "SRV001"):
        assert sanitize_identifier(name) == name


def test_sanitizer_never_returns_empty():
    """A name made entirely of stripped characters still needs *a* name.

    Downstream code keys on `vm_name`, so an empty string would be worse than
    a placeholder — it would collapse distinct rows together during de-dup.
    """
    assert sanitize_identifier("") == "unnamed"
    assert sanitize_identifier('{}"`<>') == "unnamed"
    assert sanitize_identifier("---") == "unnamed"
    # A residue that is merely odd (not dangerous) is kept rather than discarded.
    assert sanitize_identifier('${}"`') == "$"


def test_normalize_sanitizes_every_free_text_field():
    """Not just the name — os/cluster/network all reach templates too."""
    payload = 'x-${file("/etc/passwd")}'
    vms = normalize([{
        "name": payload, "cpus": 2, "memory_value": 8, "memory_unit": "GiB",
        "disk_value": 50, "disk_unit": "GiB", "os": payload, "cluster": payload,
        "network": payload, "datacenter": payload, "dns_name": payload,
    }])
    assert len(vms) == 1
    vm = vms[0]
    for field in (vm.vm_name, vm.os, vm.cluster, vm.network, vm.datacenter, vm.hostname):
        assert "${" not in (field or ""), f"interpolation survived in {field!r}"


def test_injected_interpolation_never_reaches_generated_terraform(tmp_path):
    """The end-to-end guarantee: nothing Terraform would evaluate gets emitted."""
    from iactranslate.pipeline import run_pipeline

    csv = tmp_path / "hostile.csv"
    rows = "\n".join(
        f'"{p}",2,8,50,Ubuntu 22.04,10.0.1.{i + 10}'
        for i, p in enumerate(p.replace('"', '""') for p in INJECTION_PAYLOADS)
    )
    csv.write_text("name,cpu,memory_gib,disk_gib,os,ip\n" + rows + "\n")

    out = tmp_path / "out"
    run_pipeline(input_path=str(csv), project_name="hostile", out_dir=str(out),
                 target="aws", source="generic", make_zip=False)

    for tf in out.glob("*.tf"):
        body = tf.read_text()
        # The templates legitimately emit their own interpolation (e.g.
        # "${var.project_name}-vpc"), so a blanket "no ${" assertion would be
        # wrong. What must be absent are the constructs an *attacker* needs:
        # function calls, directives, shell substitution, and a provisioner
        # block. The words "local-exec" may still appear as inert text inside a
        # quoted tag value, which is harmless.
        assert "${file(" not in body, f"{tf.name}: evaluable file() call"
        assert "%{" not in body, f"{tf.name}: HCL directive"
        assert "$(" not in body, f"{tf.name}: shell substitution"
        assert 'provisioner "' not in body, f"{tf.name}: provisioner block"


@pytest.mark.parametrize("target", ["aws", "azure", "gcp", "oci", "digitalocean"])
def test_messy_real_world_names_produce_valid_resource_names(tmp_path, target):
    """Regression for the Azure break: spaces/parens/dots are normal in CMDB
    data and must still yield names each cloud will accept.

    Asserted structurally rather than by shelling out to `tofu` — CI's
    terraform-validate job runs the real validator; this keeps the unit suite
    fast while still failing if the slugging regresses.
    """
    from iactranslate.pipeline import run_pipeline

    csv = tmp_path / "messy.csv"
    rows = "\n".join(
        f'"{n}",4,16,100,Ubuntu 22.04,10.0.1.{i + 10}' for i, n in enumerate(MESSY_REAL_NAMES)
    )
    csv.write_text("name,cpu,memory_gib,disk_gib,os,ip\n" + rows + "\n")

    out = tmp_path / target
    run_pipeline(input_path=str(csv), project_name="messy", out_dir=str(out),
                 target=target, source="generic", make_zip=False)

    compute = (out / "compute.tf").read_text()
    for line in compute.splitlines():
        stripped = line.strip()
        # Resource *name* arguments must be cloud-safe; tag values may keep the
        # original name, so only the `name = "..."` arguments are checked.
        if stripped.startswith("name ") and "=" in stripped:
            value = stripped.split("=", 1)[1].strip().strip('"')
            if value.startswith("${") or not value:
                continue  # an expression, not a literal
            assert " " not in value, f"{target}: space in resource name {value!r}"
            assert "(" not in value and ")" not in value, f"{target}: parens in {value!r}"
            assert "/" not in value, f"{target}: slash in {value!r}"
            assert "#" not in value, f"{target}: hash in {value!r}"
