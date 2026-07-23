"""Bundle all of docs/ into ONE self-contained HTML page for external sharing.

The per-doc Artifacts link to each other with repo-relative paths (and the repo
is private), so they don't work for outside readers. This build inlines the
Operations Guide, Architecture, Deployment, Roadmap, and every ADR into a single
page and rewrites every cross-document link into an in-page anchor — so the whole
documentation set works for anyone, with no GitHub access.

Mermaid code fences are emitted as `<pre class="mermaid">` so the Artifact runtime
renders them as diagrams.

Usage:  python scripts/build_docs_site.py [out.html]
Then publish the output as a shareable Artifact.
"""
from __future__ import annotations

import html as _html
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# Ordered docs: (repo path, in-page section id, heading-id prefix, nav title).
PAGES = [
    ("docs/operations-guide.md", "doc-operations-guide", "", "Operations Guide"),
    ("docs/architecture.md", "doc-architecture", "arch-", "Architecture"),
    ("docs/deployment.md", "doc-deployment", "deploy-", "Deployment"),
    ("docs/roadmap.md", "doc-roadmap", "roadmap-", "Roadmap"),
]
ADR_INDEX = "docs/adr/README.md"


def gh_slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    # GitHub keeps internal runs (so 'a & b' → 'a--b') but trims the ends.
    return value.replace(" ", "-").strip("-")


def _adr_id(path: str) -> str | None:
    m = re.search(r"adr/(\d{4})-", path)
    return f"adr-{m.group(1)}" if m else None


def _canonical(base_dir: str, path: str) -> str:
    """Resolve a relative link path against its source dir → normalized repo path."""
    import posixpath

    return posixpath.normpath(posixpath.join(base_dir, path))


def _target_anchor(base_dir: str, href: str) -> str | None:
    """Map a repo-relative doc link to an in-page anchor (or None to de-link)."""
    if href.startswith(("http://", "https://", "mailto:")):
        return href
    path, _, frag = href.partition("#")
    if not path:  # same-doc anchor — handled by the per-doc prefixer, not here
        return None
    full = _canonical(base_dir, path)
    # ADR (a specific record, or the adr index / directory)
    adr = _adr_id(full)
    if adr:
        return f"#{adr}"
    if full.rstrip("/").endswith("docs/adr") or full.endswith("docs/adr/README.md"):
        return "#doc-adr"
    for repo_path, section_id, prefix, _title in PAGES:
        if full == repo_path:
            return f"#{prefix}{frag}" if frag else f"#{section_id}"
    return None  # e.g. ../README.md — de-link for a self-contained page


def _rewrite_links(html_text: str, base_dir: str, self_prefix: str) -> str:
    def repl(m: re.Match) -> str:
        href = m.group(1)
        if href.startswith("#"):  # same-page anchor → add this doc's prefix
            return f'href="#{self_prefix}{href[1:]}"'
        target = _target_anchor(base_dir, href)
        if target is None:
            return "REMOVE_LINK"  # sentinel; unwrap the <a> below
        return f'href="{target}"'

    html_text = re.sub(r'href="([^"]+)"', repl, html_text)
    # Unwrap links we chose to de-link (keep their text).
    html_text = re.sub(r'<a href="REMOVE_LINK"[^>]*>(.*?)</a>', r"\1", html_text, flags=re.S)
    return html_text


def _mermaidify(html_text: str) -> str:
    """Turn ```mermaid code blocks into <pre class="mermaid"> the runtime renders.

    The diagram content is kept HTML-escaped: the runtime reads the element's
    textContent, where the browser decodes entities back to the real characters
    (so `--&gt;` → `-->` and `&lt;br/&gt;` → `<br/>` reach mermaid intact). Do NOT
    unescape here, or `<br/>` would parse as a real element and be lost.
    """
    return re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        lambda m: '<pre class="mermaid">' + m.group(1) + "</pre>",
        html_text, flags=re.S,
    )


def _render(md_path: Path, prefix: str, base_dir: str) -> str:
    md = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc", "sane_lists", "attr_list"],
        extension_configs={"toc": {"slugify": lambda v, s: prefix + gh_slug(v)}},
    )
    body = md.convert(md_path.read_text())
    body = _rewrite_links(body, base_dir, prefix)
    return _mermaidify(body)


_STYLE = """
:root{color-scheme:light dark;
  --bg:#fbfbfa; --panel:#ffffff; --ink:#1a1c1b; --muted:#5c6360; --faint:#8a938f;
  --line:#e6e8e6; --line-2:#d3d7d4; --accent:#0f766e; --accent-soft:#0f766e14; --code:#0b3b36;
  --nav:#ffffffcc;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){
  --bg:#0c100f; --panel:#121816; --ink:#e7ece9; --muted:#9aa6a1; --faint:#6b7671;
  --line:#1e2724; --line-2:#2a3531; --accent:#5eead4; --accent-soft:#5eead41f; --code:#9be8db;
  --nav:#0c100fcc;}}
:root[data-theme="dark"]{
  --bg:#0c100f; --panel:#121816; --ink:#e7ece9; --muted:#9aa6a1; --faint:#6b7671;
  --line:#1e2724; --line-2:#2a3531; --accent:#5eead4; --accent-soft:#5eead41f; --code:#9be8db;
  --nav:#0c100fcc;}
*{box-sizing:border-box;}
html{scroll-behavior:smooth;}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto;}}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased;}
.topnav{position:sticky;top:0;z-index:10;background:var(--nav);backdrop-filter:blur(10px);
  border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:4px 18px;align-items:center;
  padding:11px 24px;}
.topnav .brand{font-weight:680;letter-spacing:-.01em;margin-right:8px;}
.topnav a{color:var(--muted);text-decoration:none;font-size:.85rem;font-weight:520;}
.topnav a:hover{color:var(--accent);}
.wrap{max-width:820px;margin:0 auto;padding:8px 24px 120px;}
.doc{padding-top:40px;border-top:1px solid var(--line-2);margin-top:40px;scroll-margin-top:60px;}
.doc:first-of-type{border-top:0;margin-top:0;}
.doc-eyebrow{font-family:var(--mono);font-size:.72rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--accent);margin-bottom:4px;}
h1,h2,h3,h4{line-height:1.25;font-weight:660;text-wrap:balance;scroll-margin-top:60px;}
h1{font-size:2rem;letter-spacing:-.02em;margin:0 0 .3em;}
h2{font-size:1.35rem;letter-spacing:-.015em;margin:2em 0 .5em;padding-top:.4em;}
h3{font-size:1.06rem;margin:1.6em 0 .4em;}
h4{font-size:.96rem;margin:1.3em 0 .3em;color:var(--muted);}
p,ul,ol,blockquote,table,pre{margin:0 0 1em;}
a{color:var(--accent);text-decoration:none;text-underline-offset:2px;}
a:hover{text-decoration:underline;}
a:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px;}
strong{font-weight:640;color:var(--ink);}
ul,ol{padding-left:1.4em;} li{margin:.25em 0;}
blockquote{border-left:3px solid var(--accent);background:var(--accent-soft);
  margin-inline:0;padding:.6em 1em;border-radius:0 8px 8px 0;color:var(--muted);}
blockquote p:last-child{margin-bottom:0;}
:not(pre)>code{font-family:var(--mono);font-size:.86em;background:var(--line);
  color:var(--code);padding:.12em .4em;border-radius:5px;}
pre{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;overflow-x:auto;}
pre code{font-family:var(--mono);font-size:.82rem;line-height:1.6;color:var(--ink);}
pre.mermaid{background:transparent;border:0;padding:0;display:flex;justify-content:center;
  font-family:var(--sans);}
table{border-collapse:collapse;width:100%;font-size:.9rem;border:1px solid var(--line);
  border-radius:10px;overflow:hidden;display:block;overflow-x:auto;}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;}
th{background:var(--accent-soft);font-weight:640;font-size:.8rem;letter-spacing:.02em;
  text-transform:uppercase;color:var(--muted);}
td code,th code{background:transparent;padding:0;}
hr{border:0;border-top:1px solid var(--line-2);margin:2em 0;}
.intro{border:1px dashed var(--line-2);border-radius:10px;padding:16px 18px;margin:16px 0 8px;}
.intro h1{font-size:1.5rem;}
.intro p{color:var(--muted);margin-bottom:0;}
footer{max-width:820px;margin:0 auto;padding:0 24px 64px;color:var(--faint);
  font-family:var(--mono);font-size:.72rem;}
"""

def build() -> str:
    nav = ['<span class="brand">IaCTranslate — Docs</span>']
    nav += [f'<a href="#{sid}">{title}</a>' for _p, sid, _pre, title in PAGES]
    nav.append('<a href="#doc-adr">ADRs</a>')

    parts = [
        "<style>" + _STYLE + "</style>",
        '<nav class="topnav">' + "".join(nav) + "</nav>",
        '<div class="wrap">',
        '<div class="intro"><h1>IaCTranslate — Documentation</h1>'
        "<p>The complete documentation set — operations, architecture, deployment, "
        "roadmap, and decision records — in one page. Every link resolves in-page; "
        "no external access required.</p></div>",
    ]

    for repo_path, section_id, prefix, title in PAGES:
        body = _render(ROOT / repo_path, prefix, "docs")
        parts.append(
            f'<section class="doc" id="{section_id}">'
            f'<div class="doc-eyebrow">{_html.escape(title)}</div>{body}</section>'
        )

    # ADRs: index, then each record wrapped with an id for cross-links.
    adr_body = _render(ROOT / ADR_INDEX, "adrindex-", "docs/adr")
    parts.append(f'<section class="doc" id="doc-adr"><div class="doc-eyebrow">Decision Records</div>{adr_body}</section>')
    for adr_file in sorted((DOCS / "adr").glob("0*.md")):
        aid = _adr_id(f"docs/adr/{adr_file.name}")
        body = _render(adr_file, f"{aid}-", "docs/adr")
        parts.append(f'<section class="doc" id="{aid}">{body}</section>')

    parts.append("</div>")  # /.wrap
    parts.append(
        "<footer>Generated from the IaCTranslate docs/ tree "
        "(<code>python scripts/build_docs_site.py</code>). Self-contained; safe to share.</footer>"
    )
    # Make in-page anchors scroll reliably even inside a sandboxed artifact
    # iframe: intercept #-link clicks and scroll the target into view via JS
    # (default hash navigation can be a no-op when the frame is full-height).
    parts.append(_ANCHOR_JS)
    return "\n".join(parts)


_ANCHOR_JS = """<script>
(function () {
  function jump(id) {
    var el = id && document.getElementById(id);
    if (!el) return false;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    try { history.replaceState(null, '', '#' + id); } catch (e) {}
    return true;
  }
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
    if (!a) return;
    var id = decodeURIComponent(a.getAttribute('href').slice(1));
    if (jump(id)) e.preventDefault();
  });
  if (location.hash) setTimeout(function () { jump(decodeURIComponent(location.hash.slice(1))); }, 60);
})();
</script>"""


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs-site.html"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
