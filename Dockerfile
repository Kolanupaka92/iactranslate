# syntax=docker/dockerfile:1
# Multi-stage build for the IaCTranslate API. Produces a small, non-root image.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip build \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

# Install only the built wheel + its runtime deps (no build toolchain).
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
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
