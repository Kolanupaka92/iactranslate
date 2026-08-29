"""Agent providers: deterministic rule engine (default) and Claude (Anthropic)."""
from __future__ import annotations

import os
from typing import Optional

from ...targets.base import Target
from ..base import LLMProvider
from ..deidentify import DeidentifyingProvider, deidentification_enabled
from .rule_engine import RuleEngineProvider


def get_provider(target: Target, name: Optional[str] = None) -> LLMProvider:
    """Resolve the configured provider for a target.

    Selection order: explicit `name` arg -> IACTRANSLATE_LLM_PROVIDER env -> 'rule'.
    Requesting 'anthropic' without an ANTHROPIC_API_KEY (or without the SDK)
    transparently falls back to the rule engine so the pipeline always runs.

    Any provider that leaves this machine is wrapped in `DeidentifyingProvider`
    first, so customer identifiers never reach a third party. Wrapping happens
    here rather than inside a provider because it is a property of *being
    remote*, and the next provider added would otherwise have to remember it.
    """
    choice = (name or os.getenv("IACTRANSLATE_LLM_PROVIDER") or "rule").strip().lower()

    if choice in {"anthropic", "claude"}:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return RuleEngineProvider(target)
        try:
            from .anthropic_provider import AnthropicProvider

            return _protect(AnthropicProvider(target))
        except ImportError:
            return RuleEngineProvider(target)

    return RuleEngineProvider(target)


def _protect(provider: LLMProvider) -> LLMProvider:
    """De-identify inventory bound for a remote provider, unless opted out."""
    return DeidentifyingProvider(provider) if deidentification_enabled() else provider
