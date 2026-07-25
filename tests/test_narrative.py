"""AI-generated migration narrative — presentation-layer prose only.

Covers: deterministic fallback (no AI provider / no key), the honest
labeling contract (never claim 'ai' when it wasn't), and that narrative
generation never touches the plan.
"""
from iactranslate.agents import build_migration_plan
from iactranslate.assessment import assess
from iactranslate.confidence import score_plan
from iactranslate.narrative import Narrative, generate_narrative
from iactranslate.normalize import normalize
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan_and_facts(target="aws"):
    path = "tests/fixtures/rvtools_sample.xlsx"
    vms = normalize(resolve_source(path).parse(path))
    plan = build_migration_plan(vms, "narrative", get_target(target))
    assessment = assess(vms, project_name=plan.project_name, source_platform=plan.source_platform)
    confidence = score_plan(plan, vms)
    return plan, assessment, confidence


def test_default_plan_gets_a_deterministic_narrative(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan, assessment, confidence = _plan_and_facts()
    narrative = generate_narrative(plan, assessment, confidence)
    assert isinstance(narrative, Narrative)
    assert narrative.source == "deterministic"
    assert narrative.model is None
    assert str(plan.vm_count) in narrative.text


def test_anthropic_provider_used_but_no_key_still_falls_back(monkeypatch):
    # provider_used=='anthropic' can only happen if get_provider() succeeded,
    # which requires a key — but guard the no-key path explicitly anyway.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan, assessment, confidence = _plan_and_facts()
    plan.provider_used = "anthropic"  # simulate a stale/tampered record
    narrative = generate_narrative(plan, assessment, confidence)
    assert narrative.source == "deterministic"


def test_narrative_text_reflects_real_numbers():
    plan, assessment, confidence = _plan_and_facts()
    narrative = generate_narrative(plan, assessment, confidence)
    assert plan.target.upper() in narrative.text
    assert f"{assessment.readiness.score}/100" in narrative.text


def test_narrative_is_deterministic_for_rule_engine():
    plan, assessment, confidence = _plan_and_facts()
    a = generate_narrative(plan, assessment, confidence)
    b = generate_narrative(plan, assessment, confidence)
    assert a.text == b.text
    assert a.source == b.source == "deterministic"


def test_narrative_does_not_mutate_the_plan():
    plan, assessment, confidence = _plan_and_facts()
    before = plan.model_dump()
    generate_narrative(plan, assessment, confidence)
    assert plan.model_dump() == before
