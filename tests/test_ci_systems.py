"""GitOps pipelines for GitHub Actions, GitLab CI, Azure DevOps and Jenkins.

Every generated GitHub/GitLab/Azure DevOps pipeline was also validated against
the official JSON schemas (SchemaStore's github-workflow, GitLab's ci.json,
Microsoft's azure-pipelines service schema) when this was built; those schemas
are fetched, not vendored, so that check is not part of the offline suite.
Jenkins publishes no schema, so its tests here are structural.
"""
import pytest
import yaml

from iactranslate.agents import build_migration_plan
from iactranslate.cli import main
from iactranslate.gitops import (
    _CREDENTIALS,
    CI_SYSTEMS,
    GITOPS_RENDERERS,
    GitOpsNotSupportedError,
    gitops_files,
)
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target, list_targets

PIPELINE_FILE = {"github": ".github/workflows/terraform.yml", "gitlab": ".gitlab-ci.yml",
                 "azure-devops": "azure-pipelines.yml", "jenkins": "Jenkinsfile"}


@pytest.fixture(scope="module")
def plans():
    vms = normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))
    return {t: build_migration_plan(vms, "ci", get_target(t)) for t in list_targets()}


def _pipeline(plans, cloud, ci, renderer="terraform"):
    files = gitops_files(plans[cloud], renderer, ci)
    assert ".gitignore" in files
    return next(v for k, v in files.items() if k != ".gitignore"), files


# --- shape --------------------------------------------------------------------


@pytest.mark.parametrize("ci", CI_SYSTEMS)
def test_each_system_writes_its_own_file(plans, ci):
    _, files = _pipeline(plans, "aws", ci)
    assert PIPELINE_FILE[ci] in files


@pytest.mark.parametrize("ci", ["gitlab", "azure-devops"])
def test_yaml_pipelines_parse(plans, ci):
    for cloud in list_targets():
        text, _ = _pipeline(plans, cloud, ci)
        assert isinstance(yaml.safe_load(text), dict), cloud


def test_jenkinsfile_is_a_balanced_declarative_pipeline(plans):
    for cloud in list_targets():
        text, _ = _pipeline(plans, cloud, "jenkins")
        body = "\n".join(line for line in text.splitlines() if not line.startswith("//"))
        assert body.lstrip().startswith("pipeline {"), cloud
        assert body.count("{") == body.count("}"), cloud
        for stage in ("Init", "Validate", "Plan", "Apply"):
            assert f"stage('{stage}')" in body


# --- semantics: plan on PR, apply on main -------------------------------------------


def test_gitlab_plans_on_merge_requests_and_applies_on_the_default_branch(plans):
    doc = yaml.safe_load(_pipeline(plans, "aws", "gitlab")[0])
    assert doc["plan"]["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert doc["apply"]["rules"] == [{"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}]
    assert "terraform apply -auto-approve -input=false" in doc["apply"]["script"]
    assert doc["apply"]["resource_group"]  # applies are serialised


def test_azure_devops_plans_on_prs_and_applies_on_main(plans):
    doc = yaml.safe_load(_pipeline(plans, "aws", "azure-devops")[0])
    steps = {s.get("displayName"): s for s in doc["steps"]}
    assert "'PullRequest'" in steps["Plan"]["condition"]
    assert "refs/heads/main" in steps["Apply"]["condition"]
    assert "ne(variables['Build.Reason'], 'PullRequest')" in steps["Apply"]["condition"]
    assert doc["variables"] == [{"group": "iactranslate"}]


def test_jenkins_plans_on_change_requests_and_applies_on_main(plans):
    text, _ = _pipeline(plans, "aws", "jenkins")
    assert "stage('Plan') {\n            when { changeRequest() }" in text
    assert "stage('Apply') {\n            when { branch 'main' }" in text
    assert "disableConcurrentBuilds()" in text


# --- credentials: every cloud, every system ----------------------------------------


@pytest.mark.parametrize("ci", CI_SYSTEMS)
@pytest.mark.parametrize("cloud", list(_CREDENTIALS))
def test_every_cloud_gets_its_credentials_in_every_system(plans, ci, cloud):
    text, _ = _pipeline(plans, cloud, ci)
    for cred in _CREDENTIALS[cloud]:
        assert cred.secret in text, f"{cloud}/{ci}: {cred.secret} missing"
        if cred.env != cred.secret and not (ci == "jenkins" and cred.file):
            assert cred.env in text, f"{cloud}/{ci}: {cred.env} not set"


def test_secrets_are_referenced_never_inlined(plans):
    gh, _ = _pipeline(plans, "azure", "github")
    assert "ARM_CLIENT_SECRET: ${{ secrets.ARM_CLIENT_SECRET }}" in gh
    az, _ = _pipeline(plans, "azure", "azure-devops")
    # Azure DevOps does not expose secret variables to scripts unless mapped.
    assert "ARM_CLIENT_SECRET: $(ARM_CLIENT_SECRET)" in az
    jk, _ = _pipeline(plans, "azure", "jenkins")
    assert "ARM_CLIENT_SECRET = credentials('ARM_CLIENT_SECRET')" in jk


@pytest.mark.parametrize("ci", ["github", "gitlab", "azure-devops"])
def test_oci_writes_its_api_key_to_a_file_before_plan(plans, ci):
    text, _ = _pipeline(plans, "oci", ci)
    write = text.index("/tmp/oci_api_key.pem && chmod 600")
    assert "TF_VAR_private_key_path" in text
    if ci != "gitlab":  # GitLab writes it in before_script, ahead of every job
        assert write < text.index("terraform plan")


def test_jenkins_takes_the_oci_key_as_a_secret_file(plans):
    text, _ = _pipeline(plans, "oci", "jenkins")
    assert "TF_VAR_private_key_path = credentials('OCI_PRIVATE_KEY')" in text
    assert "OCI_PRIVATE_KEY (Secret file)" in text


@pytest.mark.parametrize("ci", CI_SYSTEMS)
def test_on_premises_targets_say_they_need_an_inside_runner(plans, ci):
    for cloud in ("nutanix", "proxmox"):
        assert "inside your network" in _pipeline(plans, cloud, ci)[0]
    assert "inside your network" not in _pipeline(plans, "aws", ci)[0]


@pytest.mark.parametrize("ci", CI_SYSTEMS)
def test_pulumi_pipelines_carry_the_pulumi_token(plans, ci):
    text, files = _pipeline(plans, "gcp", ci, "pulumi")
    assert "PULUMI_ACCESS_TOKEN" in text and "GOOGLE_CREDENTIALS" in text
    assert "Pulumi" in files[".gitignore"]


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize("renderer", ["cloudformation", "bicep", "cdk", "kubernetes"])
def test_renderers_with_their_own_tooling_are_refused(plans, renderer):
    assert renderer not in GITOPS_RENDERERS
    with pytest.raises(GitOpsNotSupportedError, match="its own tooling"):
        gitops_files(plans["aws"], renderer)


def test_the_for_each_layout_gets_the_terraform_pipeline(plans):
    text, _ = _pipeline(plans, "aws", "github", "terraform-foreach")
    assert "terraform apply" in text


def test_unknown_ci_system_is_refused(plans):
    with pytest.raises(GitOpsNotSupportedError, match="unknown CI system"):
        gitops_files(plans["aws"], "terraform", "circleci")


# --- CLI ----------------------------------------------------------------------------------


@pytest.mark.parametrize("ci", CI_SYSTEMS)
def test_cli_ci_flag_writes_the_pipeline_and_implies_gitops(tmp_path, ci):
    out = tmp_path / ci
    assert main(["translate", "tests/fixtures/rvtools_realistic.xlsx", "--target", "aws",
                 "--ci", ci, "--out", str(out)]) == 0
    assert (out / PIPELINE_FILE[ci]).exists()


def test_cli_refuses_gitops_for_cloudformation_before_doing_any_work(tmp_path, capsys):
    out = tmp_path / "cfn"
    assert main(["translate", "tests/fixtures/rvtools_realistic.xlsx", "--target", "aws",
                 "--renderer", "cloudformation", "--gitops", "--out", str(out)]) == 2
    assert "its own tooling" in capsys.readouterr().err
    assert not out.exists()
