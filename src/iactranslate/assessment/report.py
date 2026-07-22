"""Render an InfrastructureAssessment to JSON and a standalone HTML report.

The HTML is self-contained (inline CSS, no external assets) so it can be handed
to a client as a single file or embedded in the generated project package.
"""
from __future__ import annotations

import html
import json
from typing import List

from .models import Finding, InfrastructureAssessment, Severity

_BAND_COLOR = {
    "ready": "#16a34a",
    "minor-gaps": "#65a30d",
    "needs-work": "#d97706",
    "blocked": "#dc2626",
}
_SEV_COLOR = {
    Severity.CRITICAL: "#dc2626",
    Severity.HIGH: "#ea580c",
    Severity.MEDIUM: "#d97706",
    Severity.LOW: "#0891b2",
    Severity.INFO: "#64748b",
}


def to_json(assessment: InfrastructureAssessment, indent: int = 2) -> str:
    return json.dumps(assessment.model_dump(mode="json"), indent=indent)


def _esc(s: str) -> str:
    return html.escape(str(s))


def _affected_html(f: Finding, limit: int = 12) -> str:
    if not f.affected:
        return ""
    shown = f.affected[:limit]
    more = len(f.affected) - len(shown)
    chips = "".join(f'<span class="chip">{_esc(n)}</span>' for n in shown)
    if more > 0:
        chips += f'<span class="chip more">+{more} more</span>'
    return f'<div class="affected">{chips}</div>'


def _finding_html(f: Finding) -> str:
    color = _SEV_COLOR[f.severity]
    rec = (
        f'<p class="rec"><strong>Recommendation:</strong> {_esc(f.recommendation)}</p>'
        if f.recommendation else ""
    )
    return f"""    <div class="finding">
      <div class="finding-head">
        <span class="sev" style="background:{color}">{f.severity.value}</span>
        <span class="cat">{_esc(f.category)}</span>
        <h3>{_esc(f.title)}</h3>
        <span class="count">{f.affected_count} affected</span>
      </div>
      <p>{_esc(f.detail)}</p>
      {rec}
      {_affected_html(f)}
    </div>"""


def _stat(label: str, value: str) -> str:
    return f'<div class="stat"><span class="v">{_esc(value)}</span><span class="l">{_esc(label)}</span></div>'


def to_html(assessment: InfrastructureAssessment) -> str:
    a = assessment
    band_color = _BAND_COLOR.get(a.readiness.band, "#64748b")
    counts = a.counts_by_severity
    findings_html = "\n".join(_finding_html(f) for f in a.findings) or (
        '<p class="none">No findings — the inventory is clean.</p>'
    )
    sev_summary = " · ".join(
        f'{counts[s.value]} {s.value}' for s in
        [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW] if counts[s.value]
    ) or "no findings"

    stats: List[str] = [
        _stat("workloads", str(a.total_workloads)),
        _stat("powered on", str(a.powered_on)),
        _stat("powered off", str(a.powered_off)),
        _stat("total vCPU", str(a.total_vcpu)),
        _stat("total RAM (GiB)", f"{a.total_memory_gib:,.0f}"),
        _stat("total storage (GiB)", f"{a.total_storage_gib:,.0f}"),
        _stat("Windows / Linux", f"{a.windows_workloads} / {a.linux_workloads}"),
        _stat("utilization coverage", f"{a.utilization_coverage_pct:.0f}%"),
    ]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Migration Assessment — {_esc(a.project_name)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f8fafc; color: #0f172a; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#0b1120; color:#e2e8f0; }} .finding,.stat,.hero {{ background:#111827 !important; }} }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}
  .hero {{ background:#fff; border-radius:14px; padding:28px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .hero h1 {{ margin:0 0 4px; font-size:1.5rem; }}
  .hero .sub {{ color:#64748b; margin:0 0 20px; }}
  .score {{ display:flex; align-items:center; gap:20px; flex-wrap:wrap; }}
  .gauge {{ width:96px; height:96px; border-radius:50%; display:flex; align-items:center; justify-content:center;
           font-size:1.9rem; font-weight:700; color:#fff; background:{band_color}; flex:0 0 auto; }}
  .band {{ font-size:1.1rem; font-weight:600; color:{band_color}; text-transform:capitalize; }}
  .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; margin:24px 0; }}
  .stat {{ background:#fff; border-radius:10px; padding:14px; text-align:center; box-shadow:0 1px 2px rgba(0,0,0,.05); }}
  .stat .v {{ display:block; font-size:1.3rem; font-weight:700; }}
  .stat .l {{ display:block; font-size:.75rem; color:#64748b; margin-top:2px; }}
  h2 {{ margin:28px 0 12px; font-size:1.2rem; }}
  .finding {{ background:#fff; border-radius:12px; padding:18px 20px; margin-bottom:14px; box-shadow:0 1px 2px rgba(0,0,0,.05); }}
  .finding-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .finding-head h3 {{ margin:0; font-size:1.02rem; flex:1 1 auto; }}
  .sev {{ color:#fff; font-size:.7rem; font-weight:700; text-transform:uppercase; padding:2px 8px; border-radius:6px; letter-spacing:.03em; }}
  .cat {{ font-size:.7rem; color:#64748b; text-transform:uppercase; letter-spacing:.05em; }}
  .count {{ font-size:.75rem; color:#94a3b8; }}
  .finding p {{ margin:10px 0 0; line-height:1.5; }}
  .rec {{ color:#0f766e; }}
  @media (prefers-color-scheme: dark) {{ .rec {{ color:#5eead4; }} }}
  .affected {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }}
  .chip {{ background:#e2e8f0; color:#334155; font-size:.72rem; padding:2px 8px; border-radius:6px; }}
  .chip.more {{ background:transparent; color:#94a3b8; }}
  @media (prefers-color-scheme: dark) {{ .chip {{ background:#1e293b; color:#cbd5e1; }} }}
  .none {{ color:#16a34a; font-weight:600; }}
  footer {{ margin-top:32px; color:#94a3b8; font-size:.8rem; text-align:center; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>Migration Readiness Assessment</h1>
    <p class="sub">{_esc(a.project_name)} · source: {_esc(a.source_platform)} · {a.total_workloads} workloads</p>
    <div class="score">
      <div class="gauge">{a.readiness.score}</div>
      <div>
        <div class="band">{_esc(a.readiness.band.replace("-", " "))}</div>
        <p style="margin:4px 0 0; color:#64748b;">{_esc(a.readiness.rationale)}</p>
        <p style="margin:6px 0 0; font-size:.85rem; color:#94a3b8;">Findings: {_esc(sev_summary)}</p>
      </div>
    </div>
  </div>

  <div class="stats">
    {"".join(stats)}
  </div>

  <h2>Findings ({len(a.findings)})</h2>
{findings_html}

  <footer>Generated deterministically by IaCTranslate from your discovery export — every finding is reproducible from the input inventory. No AI in this analysis.</footer>
</div>
</body>
</html>"""
