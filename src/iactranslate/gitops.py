"""GitOps — emit a CI/CD workflow so the generated infra deploys by git.

Produces a GitHub Actions workflow (plan on pull request, apply on merge to
main) plus a `.gitignore`, tailored to the renderer (Terraform vs Pulumi) and
the target cloud's auth. The workflow references GitHub *secrets* for
credentials — it never embeds them.

Opt-in (a running apply-on-merge pipeline is a real side effect), gated by the
CLI `--gitops` flag.
"""
from __future__ import annotations

from typing import Dict

from .models import MigrationPlan

# Cloud → the credential env block (indented for a workflow step), sourced from
# GitHub Actions secrets the repo owner configures.
_CLOUD_ENV: Dict[str, str] = {
    "aws": (
        "          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}\n"
        "          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}\n"
    ),
    "azure": (
        "          ARM_CLIENT_ID: ${{ secrets.ARM_CLIENT_ID }}\n"
        "          ARM_CLIENT_SECRET: ${{ secrets.ARM_CLIENT_SECRET }}\n"
        "          ARM_SUBSCRIPTION_ID: ${{ secrets.ARM_SUBSCRIPTION_ID }}\n"
        "          ARM_TENANT_ID: ${{ secrets.ARM_TENANT_ID }}\n"
    ),
    "gcp": (
        "          GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}\n"
    ),
}

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


def _terraform_workflow(plan: MigrationPlan) -> str:
    cloud_env = _CLOUD_ENV.get(plan.target, "")
    region_env = f"          AWS_REGION: {plan.region}\n" if plan.target == "aws" else ""
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
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3

      - name: Init
        run: terraform init -input=false

      - name: Format check
        run: terraform fmt -check -recursive

      - name: Validate
        run: terraform validate -no-color

      - name: Plan
        if: github.event_name == 'pull_request'
        run: terraform plan -no-color -input=false
        env:
{cloud_env}{region_env}
      - name: Apply
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: terraform apply -auto-approve -input=false
        env:
{cloud_env}{region_env}"""


def _pulumi_workflow(plan: MigrationPlan) -> str:
    cloud_env = _CLOUD_ENV.get(plan.target, "")
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


def gitops_files(plan: MigrationPlan, renderer: str = "terraform") -> Dict[str, str]:
    """Return the GitOps files (workflow + .gitignore) for the renderer."""
    if renderer == "pulumi":
        return {
            ".github/workflows/pulumi.yml": _pulumi_workflow(plan),
            ".gitignore": _GITIGNORE_PULUMI,
        }
    return {
        ".github/workflows/terraform.yml": _terraform_workflow(plan),
        ".gitignore": _GITIGNORE_TERRAFORM,
    }
