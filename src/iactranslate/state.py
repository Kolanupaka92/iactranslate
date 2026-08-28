"""Terraform remote state backends for generated projects.

The architecture review's blunt finding: the generated Terraform declared no
backend, so it defaulted to **local state**. A `terraform.tfstate` file on one
engineer's laptop cannot be shared, cannot be locked against concurrent applies,
and loses the mapping between config and real infrastructure if that laptop
dies. No team can adopt output like that, which made every other quality in the
generated code irrelevant.

Three modes, because the honest answer depends on what the caller knows:

``local``
    No backend block, but a loud banner in `versions.tf` and a README section
    saying state is local and what to do about it. Silence was the actual bug;
    an explicit, documented choice is defensible.

**partial** (a backend named, no config)
    Emits `backend "s3" {}` plus a `backend.hcl.example`. `terraform init` then
    *fails* until the operator supplies `-backend-config=backend.hcl`. Failing
    closed is correct: we cannot invent a customer's bucket name, and quietly
    falling back to local state is how the original defect happened.

**full** (a backend named with its config)
    Emits a complete, ready-to-init block.

OCI and DigitalOcean have no first-party Terraform backend; both expose
S3-compatible object storage (Object Storage / Spaces), which the `s3` backend
drives given an endpoint override and the validation skips below — those flags
are not optional decoration, `terraform init` fails against a non-AWS endpoint
without them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: The backend each target cloud gets when the caller names a cloud but not a
#: backend type. OCI/DigitalOcean map to `s3` — see the module docstring.
BACKEND_BY_CLOUD: Dict[str, str] = {
    "aws": "s3",
    "azure": "azurerm",
    "gcp": "gcs",
    "oci": "s3",
    "digitalocean": "s3",
}

#: Keys a backend needs before it can be written as a complete block. Anything
#: less and we emit a partial block rather than guessing.
REQUIRED_KEYS: Dict[str, List[str]] = {
    "s3": ["bucket", "key", "region"],
    "azurerm": ["resource_group_name", "storage_account_name", "container_name", "key"],
    "gcs": ["bucket", "prefix"],
}

SUPPORTED = ("local", *REQUIRED_KEYS)

#: S3-compatible object stores need the endpoint pointed away from AWS and the
#: AWS-specific preflight checks disabled.
_S3_COMPATIBLE_EXTRAS = {
    "skip_credentials_validation": "true",
    "skip_region_validation": "true",
    "skip_requesting_account_id": "true",
    "skip_metadata_api_check": "true",
    "use_path_style": "true",
}

# Values that are HCL expressions or literals rather than quoted strings.
_UNQUOTED = {"true", "false"}


class UnknownBackendError(ValueError):
    pass


@dataclass
class StateBackend:
    """A resolved backend choice for one generated project."""

    kind: str = "local"
    config: Dict[str, str] = field(default_factory=dict)
    cloud: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in SUPPORTED:
            raise UnknownBackendError(
                f"unknown state backend '{self.kind}' — expected one of {', '.join(SUPPORTED)}"
            )

    @property
    def is_local(self) -> bool:
        return self.kind == "local"

    @property
    def missing_keys(self) -> List[str]:
        """Required keys the caller did not supply."""
        return [k for k in REQUIRED_KEYS.get(self.kind, []) if not self.config.get(k)]

    @property
    def is_complete(self) -> bool:
        return not self.is_local and not self.missing_keys

    def _rendered_config(self) -> Dict[str, str]:
        cfg = dict(self.config)
        # An S3 backend pointed at OCI or DigitalOcean is only usable with the
        # compatibility flags; add them rather than letting `init` fail with an
        # AWS-specific error the operator has no reason to expect.
        if self.kind == "s3" and self.cloud in ("oci", "digitalocean"):
            for key, value in _S3_COMPATIBLE_EXTRAS.items():
                cfg.setdefault(key, value)
        return cfg

    def terraform_block(self) -> str:
        """The `backend` stanza to nest inside `terraform { }`, or a banner.

        Returns an empty string for a complete-config local backend so the
        caller can decide; `versions.tf` renders the warning banner instead.
        """
        if self.is_local:
            return ""
        if not self.is_complete:
            return (
                f'  # Partial backend configuration — supply the rest at init time:\n'
                f'  #     terraform init -backend-config=backend.hcl\n'
                f'  # See backend.hcl.example. Missing: {", ".join(self.missing_keys)}\n'
                f'  backend "{self.kind}" {{}}'
            )
        lines = [f'  backend "{self.kind}" {{']
        width = max(len(k) for k in self._rendered_config())
        for key, value in self._rendered_config().items():
            rendered = value if value in _UNQUOTED else f'"{value}"'
            lines.append(f"    {key.ljust(width)} = {rendered}")
        lines.append("  }")
        return "\n".join(lines)

    def example_hcl(self) -> str:
        """`backend.hcl.example` — what the operator fills in and passes to init."""
        cfg = self._rendered_config()
        lines = [
            "# Backend configuration for `terraform init -backend-config=backend.hcl`.",
            "# Copy to backend.hcl, fill in the blanks, and keep it out of version",
            "# control if it carries anything sensitive.",
            "",
        ]
        width = max((len(k) for k in REQUIRED_KEYS.get(self.kind, ["key"])), default=3)
        for key in REQUIRED_KEYS.get(self.kind, []):
            value = cfg.get(key, "")
            lines.append(f'{key.ljust(width)} = "{value}"')
        extras = {k: v for k, v in cfg.items() if k not in REQUIRED_KEYS.get(self.kind, [])}
        if extras:
            lines.append("")
            for key, value in extras.items():
                rendered = value if value in _UNQUOTED else f'"{value}"'
                lines.append(f"{key} = {rendered}")
        if self.kind == "s3":
            lines += [
                "",
                "# State locking. Terraform 1.10+/OpenTofu 1.9+ lock via a lockfile in the",
                "# bucket; older versions need a DynamoDB table with a `LockID` hash key.",
                "# use_lockfile  = true",
                '# dynamodb_table = "terraform-locks"',
            ]
        return "\n".join(lines) + "\n"


def resolve_backend(
    cloud: str,
    kind: Optional[str] = None,
    config: Optional[Dict[str, str]] = None,
) -> StateBackend:
    """Pick a backend for `cloud`.

    `kind=None` means local — the caller said nothing, and inventing a remote
    backend they never asked for would be worse than a documented local one.
    `kind="auto"` maps to the cloud's native backend.
    """
    if kind is None:
        kind = "local"
    elif kind == "auto":
        kind = BACKEND_BY_CLOUD.get(cloud.lower(), "s3")
    return StateBackend(kind=kind, config=dict(config or {}), cloud=cloud.lower())
