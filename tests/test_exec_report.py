"""Executive report: composes plan + assessment + confidence + recommendation."""
from iactranslate.agents import build_migration_plan
from iactranslate.exec_report import build_executive_report
from iactranslate.normalize import normalize
from iactranslate.recommend import recommend
from iactranslate.sources import resolve_source
from iactranslate.targets import get_target


def _plan_and_vms(path):
    vms = normalize(resolve_source(path).parse(path))
    return build_migration_plan(vms, "rep", get_target("aws")), vms


def test_report_is_valid_html(rvtools_path):
    plan, vms = _plan_and_vms(rvtools_path)
    html = build_executive_report(plan, vms)
    assert html.lstrip().startswith("<!doctype html>")
    assert "Cloud Migration Report" in html
    # No unrendered template braces in the body.
    body = html.split("</style>", 1)[1]
    assert "{" not in body and "}" not in body


def test_report_includes_core_sections(rvtools_path):
    plan, vms = _plan_and_vms(rvtools_path)
    html = build_executive_report(plan, vms)
    for section in ("Estimated monthly cost", "Compute by tier", "Migration readiness", "Translation confidence"):
        assert section in html
    # Total cost is rendered.
    assert f"{plan.total_estimated_monthly_cost_usd:,.2f}" in html


def test_recommendation_section_optional(rvtools_path):
    plan, vms = _plan_and_vms(rvtools_path)
    without = build_executive_report(plan, vms)
    assert "Cloud recommendation" not in without
    with_rec = build_executive_report(plan, vms, recommendation=recommend(vms))
    assert "Cloud recommendation" in with_rec


def test_report_handles_empty_vms():
    plan = build_migration_plan(
        normalize(resolve_source("tests/fixtures/rvtools_sample.xlsx").parse(
            "tests/fixtures/rvtools_sample.xlsx")),
        "x", get_target("aws"),
    )
    # Pass no vms — report still renders (assessment/confidence degrade gracefully).
    html = build_executive_report(plan, [])
    assert "Cloud Migration Report" in html
