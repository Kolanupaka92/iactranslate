"""The estimate has to be defensible to the person who budgets against it."""
import pytest

from iactranslate.agents import build_migration_plan
from iactranslate.costing import EXCLUSIONS, estimate_costs
from iactranslate.exec_report import build_executive_report
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.targets import get_target


@pytest.fixture
def realistic_vms():
    path = "tests/fixtures/rvtools_realistic.xlsx"
    return normalize(parse(path))


def _plan(vms, cloud):
    return build_migration_plan(vms, "costing-test", get_target(cloud))


def test_total_exceeds_compute_because_a_real_bill_has_more_lines(realistic_vms):
    """The regression this module exists to prevent.

    `plan.total_estimated_monthly_cost_usd` sums instance cost only. On this
    estate that is 73% of the bill — storage, Windows licensing and the load
    balancers the plan itself provisions were all missing, and understating is
    the direction that hurts: the client budgets against the number.
    """
    plan = _plan(realistic_vms, "aws")
    costs = estimate_costs(plan)

    assert costs.compute == pytest.approx(plan.total_estimated_monthly_cost_usd, abs=0.01)
    assert costs.total > costs.compute
    assert costs.compute_share_pct < 80.0


def test_every_line_is_accounted_for(realistic_vms):
    plan = _plan(realistic_vms, "aws")
    costs = estimate_costs(plan)
    parts = costs.compute + costs.storage + costs.windows_licensing + costs.load_balancers
    assert costs.total == pytest.approx(parts, abs=0.05)


def test_storage_covers_every_attached_disk_not_just_the_root_volume(realistic_vms):
    plan = _plan(realistic_vms, "aws")
    costs = estimate_costs(plan)
    root_only = sum(c.root_volume_gib for c in plan.compute)
    assert costs.total_storage_gib > root_only, "extra volumes must be priced too"
    assert costs.storage > 0


def test_load_balancers_the_plan_provisions_are_priced(realistic_vms):
    plan = _plan(realistic_vms, "aws")
    costs = estimate_costs(plan)
    assert costs.load_balancer_count == len(plan.network.load_balancers)
    assert costs.load_balancer_count > 0, "fixture should exercise load balancers"
    assert costs.load_balancers > 0


def test_windows_licensing_is_charged_where_windows_actually_runs(realistic_vms):
    """Billed per provisioned vCPU, and only where the cloud runs Windows.

    DigitalOcean publishes no Windows image (ADR 0023), so its uplift is zero by
    construction rather than by omission — and that zero is exactly why it must
    not be compared on cost against clouds that do run the workload (ADR 0038).
    """
    aws = estimate_costs(_plan(realistic_vms, "aws"))
    assert aws.windows_workloads > 0
    assert aws.windows_licensing > 0

    do = estimate_costs(_plan(realistic_vms, "digitalocean"))
    assert do.windows_workloads == 0
    assert do.windows_licensing == 0.0


def test_no_committed_use_discount_is_assumed(realistic_vms):
    """Quoting a discount the customer has not purchased is how an estimate
    becomes wrong in the customer's favour."""
    costs = estimate_costs(_plan(realistic_vms, "aws"))
    assert "no committed-use discount" in costs.pricing_basis


def test_the_estimate_states_its_own_boundaries(realistic_vms):
    costs = estimate_costs(_plan(realistic_vms, "aws"))
    assert costs.excludes == EXCLUSIONS
    assert any("egress" in x.lower() for x in costs.excludes)
    assert any("backup" in x.lower() for x in costs.excludes)


def test_costing_never_mutates_the_plan(realistic_vms):
    """An analysis engine reads the plan and changes nothing (ADR 0007)."""
    plan = _plan(realistic_vms, "aws")
    before = plan.model_dump_json()
    estimate_costs(plan)
    assert plan.model_dump_json() == before


def test_every_cloud_prices_without_falling_back_to_a_default(realistic_vms):
    from iactranslate.targets import list_targets

    for cloud in list_targets():
        costs = estimate_costs(_plan(realistic_vms, cloud))
        assert costs.total > 0
        assert costs.storage > 0, f"{cloud}: storage must be priced"


def test_report_headlines_the_full_total_not_the_compute_subtotal(realistic_vms):
    """The headline and the narrative must quote the same number as the table."""
    plan = _plan(realistic_vms, "aws")
    costs = estimate_costs(plan)
    html = build_executive_report(plan, realistic_vms)

    assert f"{costs.total:,.2f}" in html
    assert "Not included in this figure" in html
    # The tier table is a compute subtotal and must say so, or its total looks
    # like it contradicts the headline.
    assert "Compute subtotal" in html
    assert "not of the" in html


def test_every_surface_quotes_the_same_total(realistic_vms, tmp_path):
    """The bundle must not contradict itself.

    A customer receives the executive report, the generated README and the
    Terraform header in one zip. When only the report was corrected, the README
    still said $16,030 beside a report saying $21,866, and the difference was
    unexplainable to anyone reading both.
    """
    from iactranslate.generator import build_files

    plan = _plan(realistic_vms, "aws")
    total = f"{estimate_costs(plan).total:,.2f}"

    files = build_files(plan, get_target("aws"))
    assert total in files["README.md"]
    assert total in files["main.tf"]
    assert total in build_executive_report(plan, realistic_vms)


def test_budget_policy_gates_on_the_real_bill(realistic_vms):
    """A budget that ignores storage and licensing does not enforce a budget.

    Compute alone is $16,030 on this estate, so a $20,000 budget used to pass
    while the actual spend is $21,866.
    """
    from iactranslate.policy.base import Severity
    from iactranslate.policy.builtins import max_monthly_cost

    plan = _plan(realistic_vms, "aws")
    compute_only = plan.total_estimated_monthly_cost_usd
    costs = estimate_costs(plan)
    budget = (compute_only + costs.total) / 2  # between the two figures
    assert compute_only < budget < costs.total

    violations = max_monthly_cost(
        plan, get_target("aws"), {"budget_usd": budget}, Severity.DENY
    )
    assert violations, "a plan over budget on the real bill must be flagged"
    assert f"{costs.total:,.2f}" in violations[0].message
