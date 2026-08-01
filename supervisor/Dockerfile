ARG DOCKERHUB_REGISTRY=docker.io
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX

FROM ${DOCKERHUB_REGISTRY}/astral/uv:0.11.7 AS uv
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG UV_INDEX_URL
ARG UV_EXTRA_INDEX_URL
ARG UV_DEFAULT_INDEX

COPY --from=uv /uv /uvx /bin/

WORKDIR /srv/supervisor
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/srv/supervisor/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "${UV_DEFAULT_INDEX}" ]; then export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX}"; fi; \
    if [ -n "${UV_INDEX_URL}" ]; then export UV_INDEX_URL="${UV_INDEX_URL}"; fi; \
    if [ -n "${UV_EXTRA_INDEX_URL}" ]; then export UV_EXTRA_INDEX_URL="${UV_EXTRA_INDEX_URL}"; fi; \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app

EXPOSE 18082
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18082"]
