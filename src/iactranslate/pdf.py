"""Server-side PDF rendering for the generated reports.

Two ways to get a PDF out of an HTML report, and this project ships both
deliberately:

**Browser Save-as-PDF** works everywhere, needs nothing installed, and is the
default answer. `print_style.PRINT_CSS` exists to make that route produce a real
document rather than a screenshot of a web page.

**This module** covers the case the browser cannot: a pipeline, a scheduled job,
or an API caller that wants a PDF without a human and a print dialog.

It is **optional** — `pip install iactranslate[pdf]` — because WeasyPrint depends
on Pango and Cairo, which are system libraries rather than wheels. Making that a
hard dependency would cost every user a compiler toolchain and roughly 60&nbsp;MB
of container image to serve a feature most of them would reach through their
browser. The container image installs the libraries so the API can render
server-side; a bare `pip install iactranslate` still works and simply reports
that this route is unavailable.

**It fails loudly and usefully.** A missing library produces the install command,
not an ImportError traceback — the person hitting this is trying to get a
document out, not debug our packaging.
"""
from __future__ import annotations

from typing import Optional

INSTALL_HINT = (
    "PDF rendering needs WeasyPrint and its system libraries. "
    "Install with `pip install 'iactranslate[pdf]'`, which also requires Pango "
    "and Cairo (macOS: `brew install pango`; Debian/Ubuntu: "
    "`apt-get install libpango-1.0-0 libpangoft2-1.0-0`). "
    "Alternatively open the HTML report in a browser and choose Save as PDF — "
    "the reports carry a print stylesheet for exactly that."
)


class PdfUnavailable(RuntimeError):
    """WeasyPrint is not installed, or its system libraries are missing."""


def available() -> bool:
    """Whether server-side rendering can run right now.

    Import is the only honest test: the Python package installs cleanly from a
    wheel and then fails at import time when Pango is absent, so checking for
    the distribution would report success on a machine that cannot render.
    """
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


def render(html: str, base_url: Optional[str] = None) -> bytes:
    """Render an HTML report to PDF bytes.

    `base_url` resolves relative references. The reports are self-contained —
    styles inline, no external assets — so it is normally unnecessary, and
    leaving it None also means a hostile report cannot pull in a local file.
    """
    try:
        from weasyprint import HTML
    except Exception as exc:
        raise PdfUnavailable(INSTALL_HINT) from exc
    return HTML(string=html, base_url=base_url).write_pdf()
