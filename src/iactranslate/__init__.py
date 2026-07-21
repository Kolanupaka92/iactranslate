"""IaCTranslate — AI infrastructure migration translator.

Turns exported infrastructure discovery reports (RVTools / VMware) into
production-ready Terraform via a deterministic pipeline:

    parse -> normalize -> agents (classify/rightsize/network) -> validate
          -> render (Jinja2) -> package (zip)

The AI only produces *structured decisions*; Python + Jinja2 emit the Terraform,
so output is reproducible and auditable.
"""

__version__ = "0.1.0"
