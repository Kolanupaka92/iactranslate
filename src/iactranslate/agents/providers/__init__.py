"""Agent providers: deterministic rule engine (default) and Claude (Anthropic)."""
from __future__ import annotations

import os
from typing import Optional

from ..base import LLMProvider
from .rule_engine import RuleEngineProvider


def get_provider(name: Optional[str] = None) -> LLMProvider:
    """Resolve the configured provider.

    Selection order: explicit `name` arg -> IACTRANSLATE_LLM_PROVIDER env -> 'rule'.
    Requesting 'anthropic' without an ANTHROPIC_API_KEY (or without the SDK)
    transparently falls back to the rule engine so the pipeline always runs.
    """
    choice = (name or os.getenv("IACTRANSLATE_LLM_PROVIDER") or "rule").strip().lower()

    if choice in {"anthropic", "claude"}:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return RuleEngineProvider()
        try:
            from .anthropic_provider import AnthropicProvider

            return AnthropicProvider()
        except ImportError:
            return RuleEngineProvider()

    return RuleEngineProvider()
