# Pure Python ETL + manifest (no Katana/Nuclei in image)
ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-alpine

WORKDIR /skill
COPY web-vuln-common/scripts /skill/common
COPY dispatcher/scripts /skill/scripts
COPY dispatcher/config /skill/config

ENTRYPOINT ["python3", "/skill/scripts/execute.py"]
