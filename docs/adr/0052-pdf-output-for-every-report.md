# 0052. PDF for every report, without making PDF a dependency

**Status:** Accepted
**Date:** 2026-08-28

## Context

Every document this tool produces is HTML: the executive report a consultant
hands to a client, the readiness assessment, the docs site and the investor
deck. "Hand it over" almost always means PDF — attached to an email, filed with
a business case, circulated to people who will not open a browser tab.

The HTML was styled entirely for screens. Printing it produced pale text on a
navy background for anyone in dark mode, tables cut off at the right-hand edge
because their container scrolls, and headers that appeared once on page one of a
five-page table.

## Decision

**Two routes, because they answer different needs.**

### A print stylesheet, always present

`print_style.PRINT_CSS` is shared by every generated document, so browser
*Save as PDF* produces a real document: A4 pages, sections kept off page
boundaries, `thead` repeated on every sheet, scroll containers expanded so wide
tables wrap instead of being sliced, and external links printing their
destination. This needs nothing installed by anyone.

**Forcing light mode is the single most important rule.** Both `@media print`
and `prefers-color-scheme: dark` match when printing from a dark-mode machine,
so the print block is appended last and wins. Without it the reader gets pale
text on white — losing it entirely — or a faithfully reproduced full-bleed navy
page. A test asserts the source ordering, because a future refactor that moves
the block would break this silently and only for some readers.

### Server-side rendering, optional

`pdf.render()` via WeasyPrint, exposed as `iactranslate report --pdf` and
`POST /projects/{id}/report?format=pdf`, for callers with no browser in the
loop — a pipeline, a scheduled job, an API client.

It is an **extra** (`pip install 'iactranslate[pdf]'`) because WeasyPrint needs
Pango and Cairo: system libraries, not wheels. Making that a hard dependency
would cost every user a toolchain and the container roughly 60 MB to serve a
feature most reach through their browser. The container image *does* install the
libraries, so the API can render server-side out of the box.

Failure is explicit and useful: a missing library produces the install command
**and** the no-install alternative, never an `ImportError` traceback. Over HTTP
it is **501, not 500** — the request was valid and the server simply cannot offer
that representation; a 500 would send the caller hunting for a bug that does not
exist. `--out report.pdf` implies `--pdf`, since writing HTML into a `.pdf` path
produces a file no viewer can open.

## Consequences

**Server-side rendering cannot be verified on a typical developer machine**, and
was not verified on this one — WeasyPrint installs from a wheel and then fails at
import because Pango is absent, which is also why `pdf.available()` tests by
importing rather than by checking installed distributions. The tests skip rather
than fail there, and a **container smoke test in CI** asserts a real `%PDF-`
document over 5 KB comes out. That is the only place the libraries reliably
exist, so it is the only place the claim is actually proven.

The project name reaches a `Content-Disposition` header, where a quote or
newline is a header-injection primitive; `_safe_filename` strips it to
`[A-Za-z0-9 ._-]` and caps it at 64 characters.

Still open: the print stylesheet is asserted by content and source ordering, not
by rendering and comparing pages — there is no visual regression test for print
layout, so a change that makes page two ugly would pass. And the artifact-hosted
teardown report is outside this system; it prints through the browser's own
defaults.
