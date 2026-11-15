"""GitOps — emit a CI/CD pipeline so the generated infra deploys by git.

Produces a pipeline (plan on pull/merge request, apply on merge to main) plus a
`.gitignore`, tailored to the renderer (Terraform vs Pulumi), the target cloud's
auth, and the CI system: GitHub Actions (the default), GitLab CI, Azure DevOps
Pipelines or Jenkins. Pipelines reference each system's *secret store* for
credentials — they never embed them.

Opt-in (a running apply-on-merge pipeline is a real side effect), gated by the
CLI `--gitops` flag; `--ci` picks the system.

Credentials are one table per cloud (`_CREDENTIALS`): the environment variable
the provider reads, and the secret it comes from. Each CI system renders the
same table its own way, so a cloud's auth is written down once. The
non-hyperscaler providers take theirs as Terraform variables, so they arrive
as `TF_VAR_*`; OCI's API key is a file, written from a secret before plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import MigrationPlan

#: CI systems a pipeline can be generated for. GitHub Actions first: it is the
#: default, and the one every earlier release generated.
CI_SYSTEMS = ("github", "gitlab", "azure-devops", "jenkins")

#: Renderers a pipeline can deploy. CloudFormation, Bicep, CDK and Kubernetes
#: have their own deployment tools, and a Terraform pipeline over their output
#: (which is what `--gitops` used to produce for them) could never succeed.
GITOPS_RENDERERS = ("terraform", "terraform-foreach", "pulumi")

#: Targets whose API is normally inside the customer's network. A cloud-hosted
#: runner cannot reach Prism Central or a Proxmox endpoint.
_ON_PREMISES = ("nutanix", "proxmox")

#: OCI's API key, a file the provider reads from `private_key_path`. Each CI
#: system supplies secret files its own way, all of them private to the job —
#: never a fixed path under /tmp, which on a shared self-hosted runner (exactly
#: what the on-premises targets need) another job could read or race.
_OCI_KEY_FILE = "oci_api_key.pem"


@dataclass(frozen=True)
class Credential:
    env: str                    # the variable the provider reads
    secret: Optional[str]       # the CI secret it comes from; None = a plain value
    value: str = ""             # the plain value, when `secret` is None
    file: bool = False          # the secret is a file; `env` gets its path


_CREDENTIALS: Dict[str, List[Credential]] = {
    "aws": [Credential("AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
            Credential("AWS_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")],
    "azure": [Credential("ARM_CLIENT_ID", "ARM_CLIENT_ID"),
              Credential("ARM_CLIENT_SECRET", "ARM_CLIENT_SECRET"),
              Credential("ARM_SUBSCRIPTION_ID", "ARM_SUBSCRIPTION_ID"),
              Credential("ARM_TENANT_ID", "ARM_TENANT_ID")],
    "gcp": [Credential("GOOGLE_CREDENTIALS", "GOOGLE_CREDENTIALS")],
    "oci": [Credential("TF_VAR_tenancy_ocid", "OCI_TENANCY_OCID"),
            Credential("TF_VAR_user_ocid", "OCI_USER_OCID"),
            Credential("TF_VAR_fingerprint", "OCI_FINGERPRINT"),
            Credential("TF_VAR_private_key_path", "OCI_PRIVATE_KEY", file=True)],
    "digitalocean": [Credential("TF_VAR_do_token", "DIGITALOCEAN_TOKEN")],
    "nutanix": [Credential("TF_VAR_nutanix_username", "NUTANIX_USERNAME"),
                Credential("TF_VAR_nutanix_password", "NUTANIX_PASSWORD")],
    "proxmox": [Credential("TF_VAR_proxmox_api_token", "PROXMOX_API_TOKEN")],
}


def _credentials(plan: MigrationPlan, tool: str = "terraform") -> List[Credential]:
    creds = list(_CREDENTIALS.get(plan.target, []))
    # The Terraform AWS provider takes its region from the environment; the
    # generated Pulumi program pins it on the provider itself.
    if plan.target == "aws" and tool == "terraform":
        creds.append(Credential("AWS_REGION", None, value=plan.region))
    return creds


def _secret_names(creds: List[Credential]) -> List[str]:
    return [c.secret for c in creds if c.secret]


def _key_files(creds: List[Credential]) -> List[Credential]:
    return [c for c in creds if c.file]


_GITIGNORE_TERRAFORM = (
    "# Terraform\n"
    ".terraform/\n"
    "*.tfstate\n"
    "*.tfstate.*\n"
    "crash.log\n"
    "*.tfvars\n"
    "!terraform.tfvars\n"
    "override.tf\n"
    "override.tf.json\n"
    ".terraform.lock.hcl\n"
)

_GITIGNORE_PULUMI = (
    "# Pulumi\n"
    "venv/\n"
    "__pycache__/\n"
    "*.pyc\n"
    "Pulumi.*.yaml\n"
)

_ON_PREM_NOTE = (
    "Nutanix and Proxmox APIs normally live inside your network, so this must run "
    "on a runner/agent that can reach them, not a cloud-hosted one."
)


# --------------------------------------------------------------------------- #
# GitHub Actions
# --------------------------------------------------------------------------- #


def _github_env(creds: List[Credential]) -> str:
    lines = []
    for c in creds:
        if c.file:
            lines.append(f"          {c.env}: ${{{{ runner.temp }}}}/{_OCI_KEY_FILE}\n")
        elif c.secret:
            lines.append(f"          {c.env}: ${{{{ secrets.{c.secret} }}}}\n")
        else:
            lines.append(f"          {c.env}: {c.value}\n")
    return "".join(lines)


def _github_key_steps(creds: List[Credential]) -> str:
    return "".join(
        f"      - name: Write {c.secret}\n"
        f"        run: printf '%s' \"${c.secret}\" > \"$RUNNER_TEMP/{_OCI_KEY_FILE}\" "
        f"&& chmod 600 \"$RUNNER_TEMP/{_OCI_KEY_FILE}\"\n"
        f"        env:\n"
        f"          {c.secret}: ${{{{ secrets.{c.secret} }}}}\n\n"
        for c in _key_files(creds)
    )


def _terraform_workflow(plan: MigrationPlan) -> str:
    creds = _credentials(plan)
    env = _github_env(creds)
    runner = "self-hosted" if plan.target in _ON_PREMISES else "ubuntu-latest"
    note = f"    # {_ON_PREM_NOTE}\n" if plan.target in _ON_PREMISES else ""
    return f"""name: Terraform
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  terraform:
    name: fmt · validate · plan / apply
{note}    runs-on: {runner}
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3

      - name: Init
        run: terraform init -input=false

      - name: Format check
        run: terraform fmt -check -recursive

      - name: Validate
        run: terraform validate -no-color

{_github_key_steps(creds)}      - name: Plan
        if: github.event_name == 'pull_request'
        run: terraform plan -no-color -input=false
        env:
{env}
      - name: Apply
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: terraform apply -auto-approve -input=false
        env:
{env}"""


def _pulumi_workflow(plan: MigrationPlan) -> str:
    cloud_env = _github_env(_credentials(plan, "pulumi"))
    return f"""name: Pulumi
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write

jobs:
  pulumi:
    name: preview / up
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt

      - name: Preview
        if: github.event_name == 'pull_request'
        uses: pulumi/actions@v5
        with:
          command: preview
          stack-name: dev
        env:
          PULUMI_ACCESS_TOKEN: ${{{{ secrets.PULUMI_ACCESS_TOKEN }}}}
{cloud_env}
      - name: Up
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        uses: pulumi/actions@v5
        with:
          command: up
          stack-name: dev
        env:
          PULUMI_ACCESS_TOKEN: ${{{{ secrets.PULUMI_ACCESS_TOKEN }}}}
{cloud_env}"""


# --------------------------------------------------------------------------- #
# GitLab CI
# --------------------------------------------------------------------------- #


def _gitlab_header(plan: MigrationPlan, creds: List[Credential], tool: str) -> str:
    secrets = _secret_names(creds)
    lines = [
        f"# Generated by IaCTranslate — GitLab CI for {tool}: plan on merge requests,",
        "# apply on the default branch.",
        "# Add these as CI/CD variables (Settings > CI/CD > Variables), Masked and",
        "# Protected:",
        f"#   {', '.join(secrets)}" if secrets else "#   (none)",
    ]
    for c in _key_files(creds):
        lines.append(f"# {c.secret} must be a *File*-type variable holding the key: GitLab writes it")
        lines.append("# to a file private to the job and puts the path in the variable.")
    if plan.target in _ON_PREMISES:
        lines.append(f"# {_ON_PREM_NOTE} Add `tags:` naming such a runner.")
    return "\n".join(lines) + "\n"


def _gitlab_variables(creds: List[Credential]) -> str:
    # Only variables that differ from their secret need mapping; a CI/CD
    # variable named AWS_ACCESS_KEY_ID is already in the job's environment.
    rows = []
    for c in creds:
        if c.file:
            rows.append(f"  {c.env}: ${c.secret}")
        elif c.secret and c.secret != c.env:
            rows.append(f"  {c.env}: ${c.secret}")
        elif not c.secret:
            rows.append(f"  {c.env}: {c.value}")
    return ("variables:\n" + "\n".join(rows) + "\n\n") if rows else ""


def _gitlab_terraform(plan: MigrationPlan) -> str:
    creds = _credentials(plan)
    return f"""{_gitlab_header(plan, creds, "Terraform")}
stages: [validate, plan, apply]

{_gitlab_variables(creds)}.terraform:
  image:
    name: hashicorp/terraform:1.9
    entrypoint: [""]
  before_script:
    - terraform init -input=false

validate:
  extends: .terraform
  stage: validate
  script:
    - terraform fmt -check -recursive
    - terraform validate -no-color
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

plan:
  extends: .terraform
  stage: plan
  script:
    - terraform plan -no-color -input=false
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

apply:
  extends: .terraform
  stage: apply
  # One apply at a time against the same state.
  resource_group: terraform-apply
  script:
    - terraform apply -auto-approve -input=false
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""


def _gitlab_pulumi(plan: MigrationPlan) -> str:
    creds = _credentials(plan, "pulumi")
    creds_with_token = [Credential("PULUMI_ACCESS_TOKEN", "PULUMI_ACCESS_TOKEN")] + creds
    return f"""{_gitlab_header(plan, creds_with_token, "Pulumi")}
stages: [preview, up]

{_gitlab_variables(creds)}.pulumi:
  image: pulumi/pulumi-python:latest
  before_script:
    - pip install -r requirements.txt
    - pulumi stack select dev --create

preview:
  extends: .pulumi
  stage: preview
  script:
    - pulumi preview --non-interactive
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"

up:
  extends: .pulumi
  stage: up
  resource_group: pulumi-up
  script:
    - pulumi up --yes --non-interactive
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
"""


# --------------------------------------------------------------------------- #
# Azure DevOps Pipelines
# --------------------------------------------------------------------------- #

_TERRAFORM_VERSION = "1.9.8"

# Plan/preview on pull requests; apply/up on a push to main (not a PR build).
_AZDO_ON_PR = "and(succeeded(), eq(variables['Build.Reason'], 'PullRequest'))"
_AZDO_ON_MAIN = (
    "and(succeeded(), eq(variables['Build.SourceBranch'], 'refs/heads/main'), "
    "ne(variables['Build.Reason'], 'PullRequest'))"
)


def _azdo_env(creds: List[Credential]) -> str:
    # Secret variables are NOT exposed to scripts automatically in Azure
    # DevOps; each must be mapped into `env:` explicitly.
    rows = []
    for c in creds:
        if c.file:
            rows.append(f"      {c.env}: $({c.secret}.secureFilePath)")
        elif c.secret:
            rows.append(f"      {c.env}: $({c.secret})")
        else:
            rows.append(f"      {c.env}: {c.value}")
    return "\n".join(rows)


def _azdo_header(plan: MigrationPlan, creds: List[Credential], tool: str) -> str:
    secrets = [c.secret for c in creds if c.secret and not c.file]
    lines = [
        f"# Generated by IaCTranslate — Azure DevOps pipeline for {tool}: plan on pull",
        "# requests, apply on main.",
        "# Create a variable group named `iactranslate` (Pipelines > Library) holding",
        "# these as secret variables, and authorize this pipeline to use it:",
        f"#   {', '.join(secrets)}" if secrets else "#   (none)",
    ]
    if _key_files(creds):
        lines.append(f"# Upload the API key as a Secure File named {_OCI_KEY_FILE} (Pipelines > Library >")
        lines.append("# Secure files); the pipeline downloads it into the job's temp directory.")
    if plan.target in _ON_PREMISES:
        lines.append(f"# {_ON_PREM_NOTE} Set `pool` to a self-hosted agent pool.")
    return "\n".join(lines) + "\n"


def _azdo_pool(plan: MigrationPlan) -> str:
    if plan.target in _ON_PREMISES:
        return "pool:\n  name: Default  # a self-hosted pool that can reach the hypervisor API\n"
    return "pool:\n  vmImage: ubuntu-latest\n"


def _azdo_terraform(plan: MigrationPlan) -> str:
    creds = _credentials(plan)
    env = _azdo_env(creds)
    key_steps = "".join(
        f"  - task: DownloadSecureFile@1\n"
        f"    name: {c.secret}\n"
        f"    displayName: Download {c.secret}\n"
        f"    inputs:\n"
        f"      secureFile: {_OCI_KEY_FILE}\n\n"
        for c in _key_files(creds)
    )
    return f"""{_azdo_header(plan, creds, "Terraform")}
trigger:
  branches:
    include: [main]
pr:
  branches:
    include: [main]

{_azdo_pool(plan)}
variables:
  - group: iactranslate

steps:
  - script: |
      curl -fsSL -o "$(Agent.TempDirectory)/terraform.zip" \\
        https://releases.hashicorp.com/terraform/{_TERRAFORM_VERSION}/terraform_{_TERRAFORM_VERSION}_linux_amd64.zip
      sudo unzip -o "$(Agent.TempDirectory)/terraform.zip" -d /usr/local/bin
    displayName: Install Terraform {_TERRAFORM_VERSION}

  - script: terraform init -input=false
    displayName: Init

  - script: terraform fmt -check -recursive
    displayName: Format check

  - script: terraform validate -no-color
    displayName: Validate

{key_steps}  - script: terraform plan -no-color -input=false
    displayName: Plan
    condition: {_AZDO_ON_PR}
    env:
{env}

  - script: terraform apply -auto-approve -input=false
    displayName: Apply
    condition: {_AZDO_ON_MAIN}
    env:
{env}
"""


def _azdo_pulumi(plan: MigrationPlan) -> str:
    creds = [Credential("PULUMI_ACCESS_TOKEN", "PULUMI_ACCESS_TOKEN")] + _credentials(plan, "pulumi")
    env = _azdo_env(creds)
    return f"""{_azdo_header(plan, creds, "Pulumi")}
trigger:
  branches:
    include: [main]
pr:
  branches:
    include: [main]

{_azdo_pool(plan)}
variables:
  - group: iactranslate

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: "3.11"

  - script: |
      curl -fsSL https://get.pulumi.com | sh
      echo "##vso[task.prependpath]$HOME/.pulumi/bin"
    displayName: Install Pulumi

  - script: pip install -r requirements.txt
    displayName: Install dependencies

  - script: pulumi stack select dev --create && pulumi preview --non-interactive
    displayName: Preview
    condition: {_AZDO_ON_PR}
    env:
{env}

  - script: pulumi stack select dev --create && pulumi up --yes --non-interactive
    displayName: Up
    condition: {_AZDO_ON_MAIN}
    env:
{env}
"""


# --------------------------------------------------------------------------- #
# Jenkins
# --------------------------------------------------------------------------- #


def _jenkins_environment(creds: List[Credential]) -> str:
    # `credentials('ID')` binds a Jenkins credential: a "Secret text" yields its
    # value, a "Secret file" yields a path — which is exactly what OCI's
    # `private_key_path` wants, so the key needs no writing step here.
    rows = []
    for c in creds:
        if c.secret:
            rows.append(f"        {c.env} = credentials('{c.secret}')")
        else:
            rows.append(f"        {c.env} = '{c.value}'")
    return "\n".join(rows)


def _jenkins_header(plan: MigrationPlan, creds: List[Credential], tool: str) -> str:
    lines = [
        f"// Generated by IaCTranslate — Jenkins pipeline for {tool}: plan on pull",
        "// requests, apply on main. Use a Multibranch Pipeline job, so `changeRequest()`",
        "// and `branch` see pull requests and branches.",
        "// Add these credentials (Manage Jenkins > Credentials), IDs exactly as shown:",
    ]
    for c in creds:
        if c.secret:
            kind = "Secret file" if c.file else "Secret text"
            lines.append(f"//   {c.secret} ({kind})")
    lines.append(f"// The agent needs {tool.lower()} on its PATH.")
    if plan.target in _ON_PREMISES:
        lines.append(f"// {_ON_PREM_NOTE}")
    return "\n".join(lines) + "\n"


def _jenkins_terraform(plan: MigrationPlan) -> str:
    creds = _credentials(plan)
    return f"""{_jenkins_header(plan, creds, "Terraform")}pipeline {{
    agent any
    options {{
        disableConcurrentBuilds()
    }}
    environment {{
{_jenkins_environment(creds)}
    }}
    stages {{
        stage('Init') {{
            steps {{
                sh 'terraform init -input=false'
            }}
        }}
        stage('Validate') {{
            steps {{
                sh 'terraform fmt -check -recursive'
                sh 'terraform validate -no-color'
            }}
        }}
        stage('Plan') {{
            when {{ changeRequest() }}
            steps {{
                sh 'terraform plan -no-color -input=false'
            }}
        }}
        stage('Apply') {{
            when {{ branch 'main' }}
            steps {{
                sh 'terraform apply -auto-approve -input=false'
            }}
        }}
    }}
}}
"""


def _jenkins_pulumi(plan: MigrationPlan) -> str:
    creds = [Credential("PULUMI_ACCESS_TOKEN", "PULUMI_ACCESS_TOKEN")] + _credentials(plan, "pulumi")
    return f"""{_jenkins_header(plan, creds, "Pulumi")}pipeline {{
    agent any
    options {{
        disableConcurrentBuilds()
    }}
    environment {{
{_jenkins_environment(creds)}
    }}
    stages {{
        stage('Install') {{
            steps {{
                sh 'pip install -r requirements.txt'
                sh 'pulumi stack select dev --create'
            }}
        }}
        stage('Preview') {{
            when {{ changeRequest() }}
            steps {{
                sh 'pulumi preview --non-interactive'
            }}
        }}
        stage('Up') {{
            when {{ branch 'main' }}
            steps {{
                sh 'pulumi up --yes --non-interactive'
            }}
        }}
    }}
}}
"""


# --------------------------------------------------------------------------- #

_PIPELINES = {
    ("github", "terraform"): (".github/workflows/terraform.yml", _terraform_workflow),
    ("github", "pulumi"): (".github/workflows/pulumi.yml", _pulumi_workflow),
    ("gitlab", "terraform"): (".gitlab-ci.yml", _gitlab_terraform),
    ("gitlab", "pulumi"): (".gitlab-ci.yml", _gitlab_pulumi),
    ("azure-devops", "terraform"): ("azure-pipelines.yml", _azdo_terraform),
    ("azure-devops", "pulumi"): ("azure-pipelines.yml", _azdo_pulumi),
    ("jenkins", "terraform"): ("Jenkinsfile", _jenkins_terraform),
    ("jenkins", "pulumi"): ("Jenkinsfile", _jenkins_pulumi),
}


class GitOpsNotSupportedError(ValueError):
    pass


def gitops_files(plan: MigrationPlan, renderer: str = "terraform", ci: str = "github") -> Dict[str, str]:
    """Return the GitOps files (pipeline + .gitignore) for the renderer and CI system."""
    if ci not in CI_SYSTEMS:
        raise GitOpsNotSupportedError(f"unknown CI system '{ci}' (available: {', '.join(CI_SYSTEMS)})")
    if renderer not in GITOPS_RENDERERS:
        raise GitOpsNotSupportedError(
            f"--gitops generates pipelines for {', '.join(GITOPS_RENDERERS)}; "
            f"'{renderer}' output is deployed with its own tooling"
        )
    tool = "pulumi" if renderer == "pulumi" else "terraform"
    path, build = _PIPELINES[(ci, tool)]
    return {path: build(plan), ".gitignore": _GITIGNORE_PULUMI if tool == "pulumi" else _GITIGNORE_TERRAFORM}
