"""Every generated document must be obtainable as a PDF.

Two routes, deliberately: a print stylesheet so browser Save-as-PDF produces a
real document with no dependency on anything, and optional server-side rendering
for callers with no browser in the loop.

Server-side rendering needs Pango and Cairo, which a developer machine usually
lacks, so those tests skip rather than fail — and the container smoke test in CI
is what actually proves a PDF comes out.
"""
import pytest
from fastapi.testclient import TestClient

from iactranslate import pdf
from iactranslate.agents import build_migration_plan
from iactranslate.api.main import app
from iactranslate.assessment import assess, to_html
from iactranslate.exec_report import build_executive_report
from iactranslate.normalize import normalize
from iactranslate.parsers import parse
from iactranslate.print_style import PRINT_CSS
from iactranslate.targets import get_target

V1 = "/v1"


@pytest.fixture(scope="module")
def vms():
    return normalize(parse("tests/fixtures/rvtools_realistic.xlsx"))


@pytest.fixture(scope="module")
def documents(vms):
    plan = build_migration_plan(vms, "Northwind", get_target("aws"))
    return {
        "executive report": build_executive_report(plan, vms),
        "assessment": to_html(assess(vms, project_name="Northwind", source_platform="vmware")),
    }


# --- the print stylesheet, which is the route that always works --------------

@pytest.mark.parametrize("name", ["executive report", "assessment"])
def test_every_report_carries_print_rules(documents, name):
    html = documents[name]
    assert "@media print" in html, f"{name} would print as a screenshot of a web page"
    assert "@page" in html


@pytest.mark.parametrize("name", ["executive report", "assessment"])
def test_tables_repeat_their_headers_across_pages(documents, name):
    """A multi-page table whose header appears once is unreadable after page 1."""
    assert "table-header-group" in documents[name]


@pytest.mark.parametrize("name", ["executive report", "assessment"])
def test_wide_content_is_not_cut_off_the_sheet(documents, name):
    """`.scroll` containers scroll on screen; on paper they must expand, or the
    right-hand columns vanish and nobody notices until the client asks."""
    html = documents[name]
    assert "overflow:visible" in html.replace(" ", "").replace("overflow: visible", "overflow:visible")


@pytest.mark.parametrize("name", ["executive report", "assessment"])
def test_printing_from_a_dark_machine_gives_light_paper(documents, name):
    """Otherwise the reader gets pale text on white, or a full-bleed navy page.

    Both `@media print` and `prefers-color-scheme: dark` match when printing from
    a dark-mode machine, so the print block must come last to win.
    """
    html = documents[name]
    dark = html.find("prefers-color-scheme: dark")
    printed = html.find("@media print")
    assert printed > dark, "the print block must override the dark palette"
    assert "color-scheme: light" in html


def test_sections_are_kept_off_page_boundaries(documents):
    """A section split across a page reads as two half-thoughts."""
    assert "page-break-inside:avoid" in documents["executive report"].replace(" ", "")


def test_external_links_print_their_destination(documents):
    """A printed link is dead text unless it says where it points."""
    assert 'a[href^="http"]::after' in PRINT_CSS
    assert 'a[href^="http"]::after' in documents["executive report"]


def test_one_stylesheet_serves_every_report(documents):
    """Two copies of print CSS is two things to forget to update."""
    for html in documents.values():
        assert PRINT_CSS.strip()[:60] in html


# --- server-side rendering, which is optional --------------------------------

def test_availability_is_tested_by_importing_not_by_metadata():
    """The wheel installs cleanly and then fails at import when Pango is absent,
    so checking the distribution would report success on a machine that cannot
    render."""
    assert isinstance(pdf.available(), bool)


def test_a_missing_renderer_explains_the_fix_rather_than_raising_importerror():
    if pdf.available():
        pytest.skip("weasyprint is installed here")
    with pytest.raises(pdf.PdfUnavailable) as e:
        pdf.render("<h1>x</h1>")
    message = str(e.value)
    assert "iactranslate[pdf]" in message
    assert "Save as PDF" in message, "the no-install route must be offered too"


@pytest.mark.skipif(not pdf.available(), reason="weasyprint needs Pango/Cairo")
def test_rendering_produces_a_real_pdf(documents):
    out = pdf.render(documents["executive report"])
    assert out.startswith(b"%PDF-")
    assert len(out) > 5000


# --- the API surface ---------------------------------------------------------

def test_report_endpoint_defaults_to_html():
    client = TestClient(app)
    pid = client.post(f"{V1}/projects", json={"name": "pdfdoc", "target": "aws"}).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"{V1}/projects/{pid}/upload",
                    files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                                 "officedocument.spreadsheetml.sheet")})
    r = client.post(f"{V1}/projects/{pid}/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


def test_an_unknown_format_is_rejected():
    client = TestClient(app)
    pid = client.post(f"{V1}/projects", json={"name": "pdfdoc2", "target": "aws"}).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"{V1}/projects/{pid}/upload",
                    files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                                 "officedocument.spreadsheetml.sheet")})
    assert client.post(f"{V1}/projects/{pid}/report?format=docx").status_code == 400


def test_pdf_without_a_renderer_is_501_not_500():
    """The request was valid; the server just cannot offer this representation.
    A 500 sends the caller hunting for a bug that does not exist."""
    if pdf.available():
        pytest.skip("weasyprint is installed here")
    client = TestClient(app)
    pid = client.post(f"{V1}/projects", json={"name": "pdfdoc3", "target": "aws"}).json()["id"]
    with open("tests/fixtures/rvtools_sample.xlsx", "rb") as f:
        client.post(f"{V1}/projects/{pid}/upload",
                    files={"file": ("i.xlsx", f, "application/vnd.openxmlformats-"
                                                 "officedocument.spreadsheetml.sheet")})
    r = client.post(f"{V1}/projects/{pid}/report?format=pdf")
    assert r.status_code == 501
    assert "iactranslate[pdf]" in r.json()["detail"]


def test_a_hostile_project_name_cannot_inject_a_header():
    """The name lands in Content-Disposition, where a quote or newline is a
    header-injection primitive."""
    from iactranslate.api.main import _safe_filename

    dirty = _safe_filename('ev"il\r\nX-Injected: yes')
    assert '"' not in dirty and "\r" not in dirty and "\n" not in dirty
    assert _safe_filename("") == "report"
    assert len(_safe_filename("x" * 500)) <= 64
