# syntax=docker/dockerfile:1.7
ARG DOCKERHUB_REGISTRY=docker.io
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL

FROM ${DOCKERHUB_REGISTRY}/astral/uv:0.11.7 AS uv
FROM ${DOCKERHUB_REGISTRY}/library/docker:27-cli AS docker-cli
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL

COPY --from=uv /uv /uvx /bin/
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi

RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap docker.io ca-certificates \
    && docker --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/executor
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/srv/executor/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${UV_DEFAULT_INDEX}" ]; then export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX}"; pypi_index_url="${UV_DEFAULT_INDEX}"; fi; \
    if [ -n "${UV_INDEX_URL}" ]; then export UV_INDEX_URL="${UV_INDEX_URL}"; fi; \
    if [ -n "${UV_EXTRA_INDEX_URL}" ]; then export UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL}"; fi; \
    if [ -n "${PIP_INDEX_URL}" ]; then export PIP_INDEX_URL="${PIP_INDEX_URL}"; fi; \
    if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL}"; fi; \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN mkdir -p /app/TRUSTGUARD_TOOLS_ROOT/NetworkScan/nmap \
    && ln -sf /usr/bin/nmap /app/TRUSTGUARD_TOOLS_ROOT/NetworkScan/nmap/nmap
ENV TRUSTGUARD_TOOLS_ROOT=/app/TRUSTGUARD_TOOLS_ROOT
ENV TOOLS_REGISTRY_YAML=/app/tools_registry.yaml
ENV TRUSTGUARD_SKILLS_ROOT=/app/skills

EXPOSE 18102
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18102"]
