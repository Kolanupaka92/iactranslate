"""GitOps — CI/CD workflow generation for the deployed package."""
from iactranslate.agents import build_migration_plan
from iactranslate.gitops import gitops_files
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan(path, target="aws"):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "g", get_target(target))


def test_terraform_workflow_has_full_pipeline(rvtools_path):
    files = gitops_files(_plan(rvtools_path), "terraform")
    assert ".github/workflows/terraform.yml" in files
    assert ".gitignore" in files
    wf = files[".github/workflows/terraform.yml"]
    assert wf.startswith("name: Terraform")
    for step in ("terraform init", "terraform fmt -check", "terraform validate",
                 "terraform plan", "terraform apply -auto-approve"):
        assert step in wf


def test_plan_on_pr_apply_on_merge(rvtools_path):
    wf = gitops_files(_plan(rvtools_path))[".github/workflows/terraform.yml"]
    # Plan is gated to pull requests; apply to pushes on main.
    assert "if: github.event_name == 'pull_request'" in wf
    assert "if: github.ref == 'refs/heads/main' && github.event_name == 'push'" in wf


def test_cloud_credentials_come_from_secrets(rvtools_path):
    for target, needle in (("aws", "AWS_ACCESS_KEY_ID"), ("azure", "ARM_CLIENT_ID"),
                           ("gcp", "GOOGLE_CREDENTIALS")):
        wf = gitops_files(_plan(rvtools_path, target))[".github/workflows/terraform.yml"]
        assert needle in wf
        assert "${{ secrets." in wf
        # No literal AWS key ever embedded.
        assert "AKIA" not in wf


def test_pulumi_workflow(rvtools_path):
    files = gitops_files(_plan(rvtools_path), "pulumi")
    assert ".github/workflows/pulumi.yml" in files
    wf = files[".github/workflows/pulumi.yml"]
    assert wf.startswith("name: Pulumi")
    assert "command: preview" in wf and "command: up" in wf
    assert "PULUMI_ACCESS_TOKEN" in wf


def test_gitignore_excludes_state(rvtools_path):
    gi = gitops_files(_plan(rvtools_path))[".gitignore"]
    assert "*.tfstate" in gi and ".terraform/" in gi
