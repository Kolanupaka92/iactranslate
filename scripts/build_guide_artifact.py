"""Render docs/operations-guide.md → a self-contained HTML page for publishing
as a shareable Artifact.

Why this exists: the Markdown-rendered Artifact does not emit heading ids (so the
table of contents can't jump) and can't resolve sibling docs (architecture.md,
adr/, …). This build produces standalone HTML that (1) gives every heading a
GitHub-style id so the TOC anchors work, and (2) rewrites cross-document relative
links to absolute GitHub URLs so they resolve from the standalone page.

Usage:  python scripts/build_guide_artifact.py [out.html]
Then publish the output to the operations-guide Artifact (same URL).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "operations-guide.md"
REPO = "https://github.com/Kolanupaka92/iactranslate"


def gh_slugify(value: str, separator: str = "-") -> str:
    """GitHub-compatible heading slug — matches the doc's TOC anchors.

    Lowercase, drop punctuation (keep word chars / spaces / hyphens), then turn
    each space into a separator WITHOUT collapsing runs (so 'Testing & CI' →
    'testing--ci', matching the '#10-testing--ci' anchors)."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    return value.replace(" ", separator)


def _resolve_link(href: str) -> str:
    """Rewrite a repo-relative doc link (from docs/) to an absolute GitHub URL."""
    if href.startswith(("http://", "https://", "#", "mailto:")):
        return href
    path, _, frag = href.partition("#")
    frag = f"#{frag}" if frag else ""
    if path.startswith("../"):
        rel = path[3:]
        return f"{REPO}/blob/main/{rel}{frag}"
    full = f"docs/{path}"
    if path.endswith("/"):
        return f"{REPO}/tree/main/{full.rstrip('/')}{frag}"
    return f"{REPO}/blob/main/{full}{frag}"


def _rewrite_links(html: str) -> str:
    return re.sub(
        r'href="([^"]+)"',
        lambda m: f'href="{_resolve_link(m.group(1))}"',
        html,
    )


_STYLE = """
:root{color-scheme:light dark;
  --bg:#fbfbfa; --panel:#ffffff; --ink:#1a1c1b; --muted:#5c6360; --faint:#8a938f;
  --line:#e6e8e6; --line-2:#d3d7d4; --accent:#0f766e; --accent-soft:#0f766e14; --code:#0b3b36;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){
  --bg:#0c100f; --panel:#121816; --ink:#e7ece9; --muted:#9aa6a1; --faint:#6b7671;
  --line:#1e2724; --line-2:#2a3531; --accent:#5eead4; --accent-soft:#5eead41f; --code:#9be8db;}}
:root[data-theme="dark"]{
  --bg:#0c100f; --panel:#121816; --ink:#e7ece9; --muted:#9aa6a1; --faint:#6b7671;
  --line:#1e2724; --line-2:#2a3531; --accent:#5eead4; --accent-soft:#5eead41f; --code:#9be8db;}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto;}}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased;}
.wrap{max-width:820px;margin:0 auto;padding:56px 24px 96px;}
h1,h2,h3,h4{line-height:1.25;font-weight:660;text-wrap:balance;scroll-margin-top:24px;}
h1{font-size:2rem;letter-spacing:-.02em;margin:0 0 .3em;}
h2{font-size:1.4rem;letter-spacing:-.015em;margin:2.4em 0 .5em;padding-top:.5em;
  border-top:1px solid var(--line);}
h2:first-of-type{border-top:0;}
h3{font-size:1.08rem;margin:1.8em 0 .4em;}
h4{font-size:.98rem;margin:1.4em 0 .3em;color:var(--muted);}
p,ul,ol,blockquote,table,pre{margin:0 0 1em;}
a{color:var(--accent);text-decoration:none;text-underline-offset:2px;}
a:hover{text-decoration:underline;}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px;}
strong{font-weight:640;color:var(--ink);}
ul,ol{padding-left:1.4em;}
li{margin:.25em 0;}
blockquote{border-left:3px solid var(--accent);background:var(--accent-soft);
  margin-inline:0;padding:.6em 1em;border-radius:0 8px 8px 0;color:var(--muted);}
blockquote p:last-child{margin-bottom:0;}
:not(pre)>code{font-family:var(--mono);font-size:.86em;background:var(--line);
  color:var(--code);padding:.12em .4em;border-radius:5px;}
pre{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;overflow-x:auto;}
pre code{font-family:var(--mono);font-size:.82rem;line-height:1.6;color:var(--ink);}
.scroll,.table-wrap{overflow-x:auto;}
table{border-collapse:collapse;width:100%;font-size:.9rem;
  border:1px solid var(--line);border-radius:10px;overflow:hidden;}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);
  vertical-align:top;}
th{background:var(--accent-soft);font-weight:640;font-size:.8rem;
  letter-spacing:.02em;text-transform:uppercase;color:var(--muted);}
tr:last-child td{border-bottom:0;}
td code,th code{background:transparent;padding:0;}
hr{border:0;border-top:1px solid var(--line-2);margin:2.2em 0;}
.doc-note{font-family:var(--mono);font-size:.72rem;color:var(--faint);
  border:1px dashed var(--line-2);border-radius:8px;padding:10px 12px;margin-bottom:28px;}
.doc-note a{color:var(--accent);}
"""

_NOTE = (
    '<div class="doc-note">Rendered from '
    '<code>docs/operations-guide.md</code>. Cross-document links open the source on GitHub '
    f'(<a href="{REPO}">{REPO.split("//")[1]}</a>, private). '
    "Regenerate with <code>python scripts/build_guide_artifact.py</code>.</div>"
)


def build() -> str:
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists", "attr_list"],
        extension_configs={"toc": {"slugify": gh_slugify}},
    )
    body = _rewrite_links(md.convert(SRC.read_text()))
    return (
        "<style>" + _STYLE + "</style>\n"
        '<div class="wrap">\n' + _NOTE + "\n" + body + "\n</div>"
    )


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "operations-guide.html"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
