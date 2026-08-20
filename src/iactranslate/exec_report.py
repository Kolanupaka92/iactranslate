"""Executive migration report — one client-facing HTML page.

Composes the deterministic outputs the pipeline already produces — the plan and
its cost, the pre-migration assessment, the confidence scoring, and (optionally)
the multi-cloud recommendation — into a single self-contained report a client or
executive can read without touching Terraform. No external assets; theme-aware.
"""
from __future__ import annotations

import html
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from .assessment import assess
from .assessment.models import Severity
from .confidence import score_plan
from .costing import estimate_costs
from .diagram import architecture_svg
from .display import display_cloud, display_source, plural
from .models import MigrationPlan, NormalizedVM
from .narrative import generate_narrative
from .recommend import Recommendation
from .waves import WaveReport, plan_waves

_SEV_COLOR = {
    Severity.CRITICAL: "#dc2626",
    Severity.HIGH: "#ea580c",
    Severity.MEDIUM: "#d97706",
    Severity.LOW: "#0891b2",
    Severity.INFO: "#64748b",
}
_BAND_COLOR = {
    "ready": "#16a34a", "minor-gaps": "#65a30d",
    "needs-work": "#d97706", "blocked": "#dc2626",
}
_CONF_COLOR = {"high": "#16a34a", "medium": "#d97706", "low": "#dc2626"}


def _esc(s: object) -> str:
    return html.escape(str(s))


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _stat(value: str, label: str, accent: Optional[str] = None) -> str:
    style = f' style="color:{accent}"' if accent else ""
    return (
        f'<div class="stat"><span class="v"{style}>{_esc(value)}</span>'
        f'<span class="l">{_esc(label)}</span></div>'
    )


def _cost_by_tier(plan: MigrationPlan) -> List[tuple]:
    agg: Dict[str, list] = defaultdict(lambda: [0, 0.0])
    for c in plan.compute:
        agg[c.tier.value][0] += 1
        agg[c.tier.value][1] += c.estimated_monthly_cost_usd
    rows = [(tier, n, cost) for tier, (n, cost) in agg.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


def _wave_section(waves: WaveReport) -> str:
    if not waves.waves:
        return ""
    rows = "\n".join(
        f"""<tr>
          <td class="num">{w.sequence}</td>
          <td class="cap">{_esc(w.name)}</td>
          <td>{_esc(', '.join(w.workloads))}</td>
          <td>{_esc(', '.join(d.split('-')[1] for d in w.depends_on) or '—')}</td>
          <td class="num">{w.estimated_downtime_minutes} min</td>
        </tr>"""
        for w in waves.waves
    )
    notes = "".join(f"<li>{_esc(n)}</li>" for n in waves.notes)
    return f"""  <section>
    <h2>Migration waves</h2>
    <p class="lead">{_esc(waves.summary)}</p>
    <div class="scroll"><table>
      <thead><tr><th class="num">Wave</th><th>Layer</th><th>Workloads</th>
        <th>Depends on</th><th class="num">Est. downtime</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    <ul class="muted">{notes}</ul>
  </section>"""


def _recommendation_section(rec: Recommendation) -> str:
    rows = "\n".join(
        f"""<tr class="{'winner' if s.cloud == rec.recommended else ''}">
          <td class="up">{_esc(s.cloud)}{' ★' if s.cloud == rec.recommended else ''}</td>
          <td class="num">{s.weighted_score:.2f}</td>
          <td class="num">{_money(s.total_monthly_cost_usd)}</td>
          <td class="num">{_money(s.annual_cost_usd)}</td>
          <td class="num">{s.cost_score:.2f}</td>
          <td class="num">{s.fit_score:.2f}</td>
          <td class="num">{s.os_score:.2f}</td>
        </tr>"""
        for s in rec.ranked
    )
    notes = "".join(f"<li>{_esc(n)}</li>" for n in rec.notes)
    notes_html = f'<ul class="muted">{notes}</ul>' if notes else ""
    return f"""  <section>
    <h2>Cloud recommendation</h2>
    <p class="lead">Recommended: <strong class="up">{_esc(rec.recommended)}</strong>
      <span class="muted">({_esc(rec.decisiveness)} lead, margin {rec.margin:.2f})</span></p>
    <div class="scroll"><table>
      <thead><tr><th>Cloud</th><th class="num">Score</th><th class="num">$/mo</th>
        <th class="num">$/yr</th><th class="num">Cost</th><th class="num">Fit</th><th class="num">OS</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>
    {notes_html}
    <p class="muted">{_esc(rec.summary)}</p>
  </section>"""


def build_executive_report(
    plan: MigrationPlan,
    vms: Optional[List[NormalizedVM]] = None,
    recommendation: Optional[Recommendation] = None,
) -> str:
    """Render the executive report as a standalone HTML string."""
    vms = vms or []
    assessment = assess(vms, project_name=plan.project_name, source_platform=plan.source_platform)
    confidence = score_plan(plan, vms)
    waves = plan_waves(plan)
    wave_section = _wave_section(waves)
    narrative = generate_narrative(plan, assessment, confidence)
    # This document is read by the customer, not the operator. Labelling which
    # engine wrote the summary is the honest-disclosure requirement from
    # ADR 0021 and stays — but the previous copy told the reader to "enable
    # --provider anthropic", putting a CLI flag for our own tool in front of
    # someone being pitched a migration.
    narrative_badge = (
        '<span class="ai-badge">✨ AI-generated summary (Claude)</span>'
        if narrative.source == "ai"
        else '<span class="ai-badge muted">Rule-based summary — generated deterministically</span>'
    )

    # `plan.total_estimated_monthly_cost_usd` is instance cost only. Headlining
    # it understated the realistic 25-VM AWS estate by 27% — storage, Windows
    # licensing and the load balancers the plan itself provisions were all
    # missing. Understating is the dangerous direction: a client budgets against
    # this number. See ADR 0039.
    costs = estimate_costs(plan)
    total_cost = costs.total
    compute_cost = costs.compute
    right_sized = [c for c in plan.compute if c.right_sized]
    band_color = _BAND_COLOR.get(assessment.readiness.band, "#64748b")
    conf_color = _CONF_COLOR.get(confidence.level, "#64748b")

    # Headline stats.
    #
    # "0/25 right-sized" was shown as a headline number whenever the inventory
    # carried no utilization data — which is most exports, since RVTools does
    # not include it by default. A bare zero reads as a failure of the tool
    # rather than a gap in the input, so when nothing could be right-sized the
    # card states the cause instead of the score.
    has_utilization = any(vm.has_utilization for vm in vms)
    right_sizing_stat = (
        _stat(f"{len(right_sized)}/{plan.vm_count}", "right-sized")
        if has_utilization
        else _stat("—", "right-sizing (no usage data)")
    )
    stats = [
        _stat(str(plan.vm_count), "workloads"),
        _stat(_money(total_cost), "est. total spend / month"),
        _stat(f"{assessment.readiness.score}", "readiness", band_color),
        _stat(f"{confidence.overall * 100:.0f}%", "confidence", conf_color),
        _stat(display_cloud(plan.target), "target cloud"),
        right_sizing_stat,
    ]

    # Itemized estimate. Zero-value lines are dropped rather than shown as
    # "$0.00" — a DigitalOcean plan has no Windows licensing because the cloud
    # has no Windows, and a row of zeros invites the reader to wonder whether
    # the line failed to compute.
    _breakdown_lines = [
        ("Compute", costs.compute, f"{plan.vm_count} instances, Linux-equivalent rate"),
        ("Block storage", costs.storage, f"{costs.total_storage_gib:,.0f} GiB attached"),
        ("Windows licensing", costs.windows_licensing,
         f"{plural(costs.windows_workloads, 'Windows workload')}, license-included"),
        ("Load balancers", costs.load_balancers,
         plural(costs.load_balancer_count, "load balancer")),
    ]
    breakdown_rows = "\n".join(
        f'<tr><td>{_esc(label)}</td><td class="num">{_money(value)}</td>'
        f'<td class="num">{(value / total_cost * 100) if total_cost else 0:.0f}%</td>'
        f'<td class="muted">{_esc(basis)}</td></tr>'
        for label, value, basis in _breakdown_lines
        if value > 0
    )
    excludes_html = "\n".join(f"<li>{_esc(x)}</li>" for x in costs.excludes)

    # Cost by tier.
    tier_rows = "\n".join(
        f'<tr><td class="cap">{_esc(t)}</td><td class="num">{n}</td>'
        f'<td class="num">{_money(cost)}</td>'
        f'<td class="num">{(cost / compute_cost * 100) if compute_cost else 0:.0f}%</td></tr>'
        for t, n, cost in _cost_by_tier(plan)
    )

    # Top findings (highest severity first, cap at 6).
    top = assessment.findings[:6]
    findings_html = "\n".join(
        f"""<div class="finding">
          <span class="sev" style="background:{_SEV_COLOR[f.severity]}">{f.severity.value}</span>
          <div><strong>{_esc(f.title)}</strong><p class="muted">{_esc(f.detail)}</p></div>
        </div>"""
        for f in top
    ) or '<p class="muted">No findings — the inventory is clean.</p>'
    more = len(assessment.findings) - len(top)
    findings_more = f'<p class="muted">+{more} more in the full assessment.</p>' if more > 0 else ""

    # Confidence factor bars.
    factor_bars = "\n".join(
        f"""<div class="bar-row"><span class="bar-label">{_esc(name)}</span>
          <div class="bar"><div class="bar-fill" style="width:{score * 100:.0f}%"></div></div>
          <span class="bar-val">{score * 100:.0f}%</span></div>"""
        for name, score in confidence.factor_averages.items()
    )
    low_conf = confidence.low_confidence()
    low_conf_html = (
        f'<p class="muted">{plural(len(low_conf), "workload")} need review: '
        + ", ".join(_esc(w.vm_name) for w in low_conf[:8])
        + ("…" if len(low_conf) > 8 else "") + ".</p>"
        if low_conf else '<p class="muted">No low-confidence workloads.</p>'
    )

    rec_section = _recommendation_section(recommendation) if recommendation else ""
    arch_svg = architecture_svg(plan)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration Report — {_esc(plan.project_name)}</title>
<style>
  :root {{ color-scheme: light dark;
    --bg:#f6f7f9; --card:#ffffff; --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --accent:#0f766e;
    --accent-soft:#0f766e14; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#0b1120; --card:#111827; --ink:#e2e8f0; --muted:#94a3b8; --line:#1e293b; --accent:#5eead4;
    --accent-soft:#5eead41f; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,Segoe UI,Roboto,sans-serif; line-height:1.5; }}
  .wrap {{ max-width:920px; margin:0 auto; padding:40px 20px 72px; }}
  header h1 {{ margin:0 0 4px; font-size:1.7rem; }}
  header .sub {{ color:var(--muted); margin:0; }}
  .stats {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin:28px 0; }}
  @media (min-width:760px) {{ .stats {{ grid-template-columns:repeat(3,1fr); }} }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; text-align:center; }}
  .stat .v {{ display:block; font-size:clamp(1.05rem,2.1vw,1.5rem); font-weight:700; overflow-wrap:anywhere; }}
  .stat .l {{ display:block; font-size:.72rem; color:var(--muted); margin-top:2px; text-transform:uppercase; letter-spacing:.04em; }}
  section {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin-bottom:18px; }}
  h2 {{ margin:0 0 14px; font-size:1.15rem; }}
  .lead {{ font-size:1.05rem; margin:0 0 12px; }}
  .up {{ text-transform:uppercase; }}
  .cap {{ text-transform:capitalize; }}
  .muted {{ color:var(--muted); }}
  h3.sub {{ margin:20px 0 8px; font-size:.9rem; font-weight:600; color:var(--muted);
            text-transform:uppercase; letter-spacing:.04em; }}
  ul.excludes {{ margin:0; padding-left:18px; color:var(--muted); font-size:.88rem; line-height:1.7; }}
  .narrative {{ font-size:.95rem; line-height:1.7; white-space:pre-line; }}
  .ai-badge {{ display:inline-block; font-size:.68rem; font-weight:600; letter-spacing:.02em;
    padding:2px 9px; border-radius:999px; background:var(--accent-soft); color:var(--accent);
    vertical-align:middle; margin-left:8px; }}
  .ai-badge.muted {{ background:var(--line); color:var(--muted); font-weight:500; }}
  table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
  th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; }}
  th.num,td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  tr.winner td {{ font-weight:700; }}
  .scroll {{ overflow-x:auto; }}
  .finding {{ display:flex; gap:12px; align-items:flex-start; padding:10px 0; border-bottom:1px solid var(--line); }}
  .finding:last-child {{ border-bottom:none; }}
  .finding p {{ margin:2px 0 0; font-size:.88rem; }}
  .sev {{ color:#fff; font-size:.65rem; font-weight:700; text-transform:uppercase; padding:2px 8px; border-radius:6px; flex:0 0 auto; margin-top:2px; }}
  .bar-row {{ display:flex; align-items:center; gap:12px; margin:8px 0; }}
  .bar-label {{ width:110px; text-transform:capitalize; font-size:.85rem; }}
  .bar {{ flex:1; height:8px; background:var(--line); border-radius:99px; overflow:hidden; }}
  .bar-fill {{ height:100%; background:var(--accent); border-radius:99px; }}
  .bar-val {{ width:44px; text-align:right; font-variant-numeric:tabular-nums; font-size:.82rem; }}
  footer {{ margin-top:28px; text-align:center; color:var(--muted); font-size:.78rem; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Cloud Migration Report</h1>
    <p class="sub">{_esc(plan.project_name)} · {_esc(display_source(plan.source_platform))} → {_esc(display_cloud(plan.target))} ({_esc(plan.region)}) · {date.today().isoformat()}</p>
  </header>

  <div class="stats">{"".join(stats)}</div>

  <section>
    <h2>Summary {narrative_badge}</h2>
    <p class="narrative">{_esc(narrative.text)}</p>
  </section>

  <section>
    <h2>Estimated monthly cost — {_money(total_cost)}</h2>
    <div class="scroll"><table>
      <thead><tr><th>Line</th><th class="num">$/mo</th><th class="num">Share</th><th>Basis</th></tr></thead>
      <tbody>{breakdown_rows}</tbody>
      <tfoot><tr><td><strong>Total</strong></td>
        <td class="num"><strong>{_money(total_cost)}</strong></td>
        <td class="num">100%</td><td></td></tr></tfoot>
    </table></div>
    <p class="muted">{_esc(costs.pricing_basis)}. Committed-use discounts (Reserved
      Instances, Savings Plans, CUDs) typically reduce compute 30–60% and are deliberately
      not applied here.</p>
    <h3 class="sub">Not included in this figure</h3>
    <ul class="excludes">{excludes_html}</ul>
  </section>

  <section>
    <h2>Compute by tier</h2>
    <div class="scroll"><table>
      <thead><tr><th>Tier</th><th class="num">Workloads</th><th class="num">$/mo</th><th class="num">Share</th></tr></thead>
      <tbody>{tier_rows}</tbody>
      <tfoot><tr><td class="cap"><strong>Compute subtotal</strong></td><td class="num"><strong>{plan.vm_count}</strong></td>
        <td class="num"><strong>{_money(compute_cost)}</strong></td><td class="num">100%</td></tr></tfoot>
    </table></div>
    <p class="muted">Shares are of the compute subtotal, not of the {_money(total_cost)} total above.
      Pricing: {'live market rates' if plan.pricing_source == 'live' else 'curated static rates'}, on-demand.
      {len(right_sized)} of {plan.vm_count} workloads were right-sized to observed utilization.</p>
  </section>

  <section>
    <h2>Target architecture</h2>
    <div class="scroll">{arch_svg}</div>
  </section>

{wave_section}

{rec_section}

  <section>
    <h2>Migration readiness — {assessment.readiness.score}/100
      <span style="color:{band_color}; text-transform:capitalize;">({_esc(assessment.readiness.band.replace('-', ' '))})</span></h2>
    <p class="muted">{_esc(assessment.readiness.rationale)}</p>
    {findings_html}
    {findings_more}
  </section>

  <section>
    <h2>Translation confidence — {confidence.overall * 100:.0f}%
      <span style="color:{conf_color}; text-transform:capitalize;">({_esc(confidence.level)})</span></h2>
    {factor_bars}
    {low_conf_html}
  </section>

  <footer>Generated deterministically by IaCTranslate from your discovery export.
    Every figure is reproducible from the source inventory.</footer>
</div>
</body>
</html>"""
