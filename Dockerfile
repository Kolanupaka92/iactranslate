# syntax=docker/dockerfile:1
# Multi-stage build for the IaCTranslate API. Produces a small, non-root image.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

# Apply outstanding OS security updates. The base image is rebuilt on its own
# schedule and lags Debian's security archive — CI's Trivy gate caught three
# fixed HIGH CVEs this way, including CVE-2026-14456 in libssl3t64, where the
# patched package was already published and simply not yet in the base.
#
# Upgrading at build time rather than pinning individual packages keeps this
# from becoming a list someone has to maintain by hand.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    # Pango/Cairo back WeasyPrint, so the API can return a PDF without a
    # browser in the loop. Installed here rather than made a Python dependency:
    # a bare `pip install iactranslate` should not need a compiler toolchain.
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

# Install only the built wheel + its runtime deps (no build toolchain).
COPY --from=builder /wheels /wheels
# `psycopg[binary]` ships wheels, so this still needs no compiler in the
# runtime stage. Installed unconditionally because the image is the artifact a
# hosted deployment runs, and that deployment is the case that needs PostgreSQL
# (ADR 0063) — a SQLite-only image would make the store an image choice rather
# than a configuration one.
# `cryptography` is what makes encryption at rest actually available. It is an
# optional extra for the library — a bare `pip install iactranslate` should not
# need it — but this image is what a hosted deployment runs, and there
# IACTRANSLATE_ENCRYPTION_KEY is set. Without the package the API fails closed
# and refuses every upload (ADR 0043), which is the correct behaviour and a
# useless deployment. Both ship wheels, so the runtime stage still needs no
# compiler.
RUN pip install --no-cache-dir /wheels/*.whl weasyprint \
        "psycopg[binary,pool]>=3.1" "cryptography>=42.0" \
    && rm -rf /wheels

USER appuser
EXPOSE 8000

# Bounds are env-overridable (see iactranslate/config.py).
ENV IACTRANSLATE_MAX_UPLOAD_MB=25 \
    IACTRANSLATE_MAX_VMS=5000 \
    IACTRANSLATE_MAX_PROJECTS=200

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "iactranslate.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
