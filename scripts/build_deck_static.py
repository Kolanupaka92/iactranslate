"""Render docs/investor-deck.html (the interactive slide deck) into a STATIC,
publicly-shareable page.

claude.ai blocks public sharing of artifacts that run JavaScript, and the deck's
navigation is all JS. This transform drops the script + JS-driven chrome and
stacks the 12 slides into one scrollable page — same visual design, no <script>
— so it can be shared publicly. The interactive version stays at
docs/investor-deck.html for presenting live.

Usage:  python scripts/build_deck_static.py [out.html]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "investor-deck.html"


def build() -> str:
    html = SRC.read_text()

    # 1) Drop everything from the first chrome element onward (chrome + <script>).
    html = html[: html.index('<div class="hint">')].rstrip()

    # 2) Let the page scroll (was a fixed, overflow-hidden deck).
    html = html.replace(
        "-webkit-font-smoothing:antialiased; overflow:hidden;",
        "-webkit-font-smoothing:antialiased;",
    )
    html = html.replace("html,body{margin:0;height:100%;}", "html,body{margin:0;}")
    html = html.replace(".deck{position:fixed;inset:0;}", ".deck{}")

    # 3) Slides: absolute/hidden overlay → stacked, full-height, scrollable sections.
    slide_rule = re.search(r"\.slide\{.*?\n  \}", html, flags=re.S)
    if slide_rule:
        html = html.replace(
            slide_rule.group(0),
            ".slide{\n"
            "    display:flex; flex-direction:column; justify-content:center;\n"
            "    min-height:100vh; padding:clamp(44px,9vh,110px) clamp(28px,6vw,96px);\n"
            "    border-top:1px solid var(--line);\n"
            "    background:\n"
            "      radial-gradient(1200px 600px at 82% -10%, rgba(47,206,151,.10), transparent 60%),\n"
            "      radial-gradient(900px 500px at -10% 110%, rgba(31,157,114,.08), transparent 60%),\n"
            "      var(--ground);\n"
            "  }\n"
            "  .slide:first-child{border-top:0;}",
        )
    # 4) Remove now-defunct active/transition rules.
    html = html.replace(".slide.active{opacity:1;visibility:visible;transform:none;}", "")
    html = re.sub(
        r"@media \(prefers-reduced-motion: reduce\)\{\.slide\{[^}]*\}\}",
        "", html,
    )

    # 5) A top bar. The deck's floating "IaCTranslate" mark sits in the chrome
    #    cut off in step 1, which left the public overview with no way home.
    topbar_css = (
        ".topbar{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:20px;"
        "padding:12px clamp(20px,5vw,64px);background:rgba(7,11,10,.88);backdrop-filter:blur(8px);"
        "border-bottom:1px solid var(--line);font-family:var(--mono);font-size:13px;}"
        ".topbar a{color:var(--muted);text-decoration:none;}"
        ".topbar a:hover{color:var(--ink);text-decoration:underline;}"
        ".topbar .home{color:var(--ink);font-weight:600;display:flex;align-items:center;gap:9px;}"
        ".topbar .home .dot{width:8px;height:8px;border-radius:2px;background:var(--accent);}"
        ".topbar .push{margin-left:auto;}"
        ".topbar .cta{color:var(--accent);}"
    )
    html = html.replace("</style>", topbar_css + "\n</style>", 1)
    html = html.replace(
        '<div class="deck" id="deck">',
        '<nav class="topbar" aria-label="Site">'
        '<a class="home" href="https://iactranslate.com" title="IaCTranslate home">'
        '<span class="dot"></span>IaCTranslate</a>'
        '<a class="push" href="https://docs.iactranslate.com/">Docs</a>'
        '<a class="cta" href="https://iactranslate.com/console">Open the console →</a>'
        "</nav>\n"
        '<div class="deck" id="deck">',
        1,
    )

    # 6) Title reflects the static variant.
    html = html.replace(
        "<title>IaCTranslate — Investor Deck</title>",
        "<title>IaCTranslate — Investor Overview</title>",
    )
    return html


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "investor-deck-static.html"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
