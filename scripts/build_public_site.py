"""Build the public docs site (docs.iactranslate.com) into a directory.

    python scripts/build_public_site.py <out_dir>

Writes `index.html` (every doc in one page), `overview.html` (the investor
overview) and a `vercel.json`. The Vercel project `iactranslate-docs` runs this
on every push to `main` (see the repo-root `vercel.json`), and
`scripts/deploy_public_site.sh` runs it for a manual deploy, so the two can't
drift apart.

Before this existed the project had no build command: each push "deployed" the
raw repository, which has no index page, and the public site stayed on a manual
deploy from months earlier without anyone noticing.
"""
from __future__ import annotations

import json
import subprocess  # nosec B404 — runs this repo's own build scripts with fixed arguments
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = "https://iactranslate.com"


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    index, overview = out / "index.html", out / "overview.html"
    for script, target in (("build_docs_site.py", index), ("build_deck_static.py", overview)):
        subprocess.run([sys.executable, str(ROOT / "scripts" / script), str(target)],  # nosec B603
                       check=True, cwd=ROOT)

    page = index.read_text()
    nav_end = '<a href="#doc-adr">ADRs</a></nav>'
    if nav_end in page:
        page = page.replace(nav_end, '<a href="#doc-adr">ADRs</a>'
                            '<a href="/overview" style="margin-left:auto;color:var(--accent)">'
                            "Investor overview →</a></nav>")
    index.write_text(page)
    deck = overview.read_text()
    # The overview pointed at a private claude.ai copy of the docs; the public
    # site is the one visitors can open.
    deck = deck.replace("https://claude.ai/code/artifact/ecc2a04b-904e-431b-9ab0-7a8856963c20", "/")
    overview.write_text(deck)
    # The old vercel.app address forwards to the custom domain, so a link shared
    # before the move lands on the current docs instead of a stale copy.
    (out / "vercel.json").write_text(json.dumps({"cleanUrls": True, "redirects": [{
        "source": "/:path*", "has": [{"type": "host", "value": "iactranslate-docs.vercel.app"}],
        "destination": "https://docs.iactranslate.com/:path*", "permanent": True}]}) + "\n")
    print(f"built {index.name} and {overview.name} into {out} (home link: {HOME})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_public_site.py <out_dir>")
    main(Path(sys.argv[1]))
