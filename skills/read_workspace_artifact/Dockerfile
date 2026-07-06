ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
