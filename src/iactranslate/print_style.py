"""Print rules shared by every generated HTML report.

These documents get handed to a client, and "handed over" almost always means
PDF. Browser *Save as PDF* is the route that needs no dependency on our side or
theirs, so it has to produce a real document rather than a screenshot of a web
page — page breaks in sensible places, table headers repeated on every sheet,
and nothing sliced off the right-hand edge.

Injected as a substitution value rather than written inline in the report
templates: those are f-strings, so every brace would need doubling, and a
stylesheet full of `{{` is a stylesheet nobody will edit correctly.

The single most important rule here is forcing light mode. A reader whose OS is
in dark mode would otherwise print pale text on a background their printer either
renders as white — losing the text entirely — or faithfully reproduces as a
full-bleed navy page. Both outcomes are worse than not offering print at all.
"""
from __future__ import annotations

PRINT_CSS = """
  /* ---------------------------------------------------------------- print --
     See iactranslate/print_style.py for why these rules exist.              */
  @page {
    size: A4;
    margin: 18mm 14mm;
  }
  @media print {
    /* Force light. Both this block and any `prefers-color-scheme: dark` block
       match when printing from a dark-mode machine, so this must come last in
       source order — it does, because it is appended to the end of the sheet. */
    :root {
      color-scheme: light;
      --bg:#ffffff; --card:#ffffff; --ink:#0f172a; --muted:#475569;
      --line:#cbd5e1; --accent:#0f766e; --accent-soft:#0f766e14;
      --ground:#ffffff; --surface:#ffffff; --ink-2:#334155; --faint:#64748b;
    }
    html, body {
      background:#ffffff !important;
      color:#0f172a !important;
      font-size:10.5pt;
    }
    .wrap { max-width:none !important; padding:0 !important; }

    /* Reports that style cards directly rather than through variables. */
    .finding, .stat, .hero, .card, .col, .verdict, .callout, .ev {
      background:#ffffff !important;
      box-shadow:none !important;
      break-inside:avoid;
      page-break-inside:avoid;
    }

    /* A section split across a page boundary reads as two half-thoughts. */
    section { break-inside:avoid; page-break-inside:avoid; margin-bottom:14pt; }
    h1, h2, h3 { break-after:avoid; page-break-after:avoid; }

    /* Wide content scrolls on screen. On paper it must wrap, or the right-hand
       columns are simply cut off the sheet and nobody notices until the client
       asks what happened to the cost column. */
    .scroll { overflow:visible !important; }
    table { width:100% !important; min-width:0 !important; font-size:9.5pt; }
    thead { display:table-header-group; }   /* repeat headers on every page */
    tfoot { display:table-footer-group; }
    tr, img, pre { break-inside:avoid; page-break-inside:avoid; }

    /* Grids that reflow to one column on a narrow screen would otherwise print
       as a single tall column, wasting most of the page. */
    .stats { grid-template-columns:repeat(3,1fr) !important; }

    /* A printed link is dead text unless it says where it points. Restricted to
       external links: printing the href of every in-page anchor is noise. */
    a[href^="http"]::after {
      content:" (" attr(href) ")";
      font-size:8.5pt; color:#475569; word-break:break-all;
    }

    /* Anything that only makes sense as a control. */
    .no-print, button, nav { display:none !important; }
  }
"""
