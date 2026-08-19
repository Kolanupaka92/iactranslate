"""CLI surface tests, focused on the paths a first-time evaluator hits."""
from __future__ import annotations

from pathlib import Path

# -- the zero-friction try (ADR 0034) --------------------------------------


def test_demo_runs_with_no_input_file(tmp_path, capsys):
    """A stranger must be able to evaluate this without owning an inventory.

    Requiring a real RVTools/CMDB export to try the tool means the first step
    is handing your infrastructure to software you have never run — the single
    biggest barrier to someone testing it at all.
    """
    from iactranslate.cli import main

    out = tmp_path / "demo"
    assert main(["demo", "--out", str(out)]) == 0

    printed = capsys.readouterr().out
    assert "Nothing is uploaded" in printed
    # Real generated Terraform, not a canned message.
    assert (out / "compute.tf").exists()
    assert (out / "main.tf").exists()
    assert 'resource "aws_instance"' in (out / "compute.tf").read_text()


def test_sample_estate_ships_inside_the_package():
    """It must resolve from the installed package, not the working directory,
    or `demo` breaks inside the Docker image and in an installed wheel."""
    from iactranslate.cli import sample_estate_path

    p = sample_estate_path()
    assert p.exists()
    assert p.parent.name == "samples"
    assert p.is_relative_to(Path(__import__("iactranslate").__file__).parent)


def test_demo_works_for_every_target(tmp_path):
    """The first thing an evaluator does is try their own cloud."""
    from iactranslate.cli import main

    for target in ("aws", "azure", "gcp", "oci", "digitalocean"):
        assert main(["demo", "--target", target, "--out", str(tmp_path / target)]) == 0
        assert (tmp_path / target / "compute.tf").exists()
