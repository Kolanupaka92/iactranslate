"""Multi-renderer: the same plan rendered as Terraform or Pulumi."""
import py_compile

import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.normalize import normalize
from iactranslate.renderers import (
    UnknownRendererError,
    list_renderers,
    render,
)
from iactranslate.renderers.pulumi import RendererNotSupportedError
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(path, target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "r", get_target(target)), vms


def test_registry_lists_both():
    assert set(list_renderers()) == {"terraform", "pulumi"}


def test_unknown_renderer_raises(rvtools_path):
    plan, _ = _plan(rvtools_path)
    with pytest.raises(UnknownRendererError):
        render("cloudformation", plan, get_target("aws"))


def test_pulumi_program_compiles(rvtools_path, tmp_path):
    plan, _ = _plan(rvtools_path)
    files = render("pulumi", plan, get_target("aws"))
    assert set(files) >= {"__main__.py", "Pulumi.yaml", "requirements.txt", "README.md"}
    main = tmp_path / "main.py"
    main.write_text(files["__main__.py"])
    py_compile.compile(str(main), doraise=True)  # raises on a syntax error


def test_pulumi_covers_core_resources(rvtools_path):
    plan, _ = _plan(rvtools_path)
    main = render("pulumi", plan, get_target("aws"))["__main__.py"]
    assert "aws.ec2.Vpc(" in main
    assert "aws.ec2.Subnet(" in main
    assert "aws.ec2.SecurityGroup(" in main
    assert "aws.ec2.Instance(" in main
    assert "aws.ec2.get_ami(" in main
    # One instance resource per workload.
    assert main.count("aws.ec2.Instance(") == plan.vm_count


def test_pulumi_only_supports_aws(rvtools_path):
    plan, _ = _plan(rvtools_path, target="azure")
    with pytest.raises(RendererNotSupportedError):
        render("pulumi", plan, get_target("azure"))


def test_terraform_renderer_matches_generator(rvtools_path):
    from iactranslate.generator import build_files

    plan, _ = _plan(rvtools_path)
    assert render("terraform", plan, get_target("aws")) == build_files(plan, get_target("aws"))
