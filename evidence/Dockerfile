# syntax=docker/dockerfile:1.7
ARG DOCKERHUB_REGISTRY=docker.io
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL

FROM ${DOCKERHUB_REGISTRY}/astral/uv:0.11.7 AS uv
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL

COPY --from=uv /uv /uvx /bin/

WORKDIR /srv/evidence

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/srv/evidence/.venv/bin:$PATH" \
    WORKSPACE_ROOT=/data/workspace

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${UV_DEFAULT_INDEX}" ]; then export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX}"; pypi_index_url="${UV_DEFAULT_INDEX}"; fi; \
    if [ -n "${UV_INDEX_URL}" ]; then export UV_INDEX_URL="${UV_INDEX_URL}"; fi; \
    if [ -n "${UV_EXTRA_INDEX_URL}" ]; then export UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL}"; fi; \
    if [ -n "${PIP_INDEX_URL}" ]; then export PIP_INDEX_URL="${PIP_INDEX_URL}"; fi; \
    if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then export PIP_EXTRA_INDEX_URL="${PIP_EXTRA_INDEX_URL}"; fi; \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
RUN mkdir -p /data/workspace

EXPOSE 18103

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18103"]
