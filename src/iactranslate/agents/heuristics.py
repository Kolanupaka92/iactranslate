"""Shared deterministic heuristics for environment / tier / AMI detection.

Used directly by the rule engine and as guardrails/fallbacks for LLM output.
"""
from __future__ import annotations

from ..models import Environment, NormalizedVM, Tier

_ENV_TOKENS = [
    (Environment.PRODUCTION, ("prod", "prd", "-p-", "production")),
    (Environment.STAGING, ("stg", "stag", "staging", "preprod", "pre-prod")),
    (Environment.TEST, ("test", "tst", "uat", "qa")),
    (Environment.DEVELOPMENT, ("dev", "development", "sandbox", "sbx")),
]

_TIER_TOKENS = [
    (Tier.DATABASE, ("db", "sql", "database", "postgres", "mysql", "oracle", "mongo", "mariadb", "mssql")),
    (Tier.CACHE, ("cache", "redis", "memcache", "memcached")),
    (Tier.WEB, ("web", "www", "nginx", "iis", "apache", "frontend", "fe", "lb", "proxy")),
    (Tier.APP, ("app", "api", "backend", "be", "svc", "service", "worker", "middleware")),
]


def _haystack(vm: NormalizedVM) -> str:
    parts = [vm.vm_name]
    if vm.hostname:
        parts.append(vm.hostname)
    return " ".join(parts).lower()


def detect_environment(vm: NormalizedVM) -> Environment:
    text = _haystack(vm)
    for env, tokens in _ENV_TOKENS:
        if any(t in text for t in tokens):
            return env
    return Environment.UNKNOWN


def detect_tier(vm: NormalizedVM) -> Tier:
    text = _haystack(vm)
    for tier, tokens in _TIER_TOKENS:
        if any(t in text for t in tokens):
            return tier
    return Tier.OTHER


def detect_ami_key(os_string: str | None) -> str:
    if not os_string:
        return "amazon-linux-2"
    s = os_string.lower()
    if "windows" in s:
        if "2019" in s:
            return "windows-2019"
        if "2016" in s:
            return "windows-2016"
        return "windows-2022"
    if "red hat" in s or "rhel" in s:
        return "rhel-9"
    if "ubuntu" in s:
        return "ubuntu-22.04"
    if "suse" in s or "sles" in s:
        return "sles-15"
    if "centos" in s:
        return "centos-7"
    return "amazon-linux-2"
