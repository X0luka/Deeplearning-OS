FROM ghcr.io/astral-sh/uv:0.10.7@sha256:edd1fd89f3e5b005814cc8f777610445d7b7e3ed05361f9ddfae67bebfe8456a AS uv
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS base
ENV LANG=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ca-certificates curl git libglib2.0-0t64 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/dlos

FROM base AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY components/ components/
COPY scripts/ scripts/
COPY requirements/ requirements/
COPY assets/ assets/
COPY toolchain.json LICENSE THIRD_PARTY_NOTICES.md ./
RUN python3 scripts/init_development.py \
    && python3 scripts/prepare_dependencies.py --uv /usr/local/bin/uv \
    && python3 scripts/install_node.py
ENV PATH="/opt/dlos/.local/tools/node/bin:${PATH}"
# Build isolation is provided by the build container; no host mounts or credentials.
RUN cd components/PaperReader && npm ci --ignore-scripts --no-audit --no-fund && npm run build \
    && cd ../GitHub-Wiki/frontend && npm ci --ignore-scripts --no-audit --no-fund \
    && npm run check && npm run build
RUN rm -rf components/PaperReader/node_modules components/GitHub-Wiki/frontend/node_modules \
    .local/uv-cache .local/tool-downloads .local/setup-home/.npm

FROM build AS pdf-build
RUN python3 scripts/prepare_pdf_cpu.py install --uv /usr/local/bin/uv \
    && components/AgentOS-K/.venv/bin/python scripts/download_pdf_assets.py \
    && .local/pdf-cpu/venv/bin/python scripts/prefetch_pdf_ocr.py \
    && .local/pdf-cpu/venv/bin/python scripts/verify_pdf_environment.py
RUN rm -rf .local/pdf-cpu/hf .local/pdf-cpu/cache

FROM base AS core
COPY --from=build /opt/dlos/ /opt/dlos/
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin dlos \
    && mkdir -p /opt/dlos/.local/instances /opt/dlos/.local/instance-locks \
    && chown 10001:10001 /opt/dlos/.local/instances /opt/dlos/.local/instance-locks
USER 10001:10001
EXPOSE 18877 18878 18879
HEALTHCHECK --interval=15s --timeout=12s --start-period=60s --retries=4 \
    CMD ["components/AgentOS-K/.venv/bin/python", "-m", "aos.container_instance", "--healthcheck"]
CMD ["components/AgentOS-K/.venv/bin/python", "-m", "aos.container_instance"]

# Default user experience retains enhanced PDF/OCR assets and the pinned CPU parser.
FROM core AS full
COPY --from=pdf-build /opt/dlos/.local/pdf-cpu/ /opt/dlos/.local/pdf-cpu/
