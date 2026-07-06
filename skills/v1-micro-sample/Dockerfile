ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
RUN if [ -n "${PIP_INDEX_URL}" ] || [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
        { \
            echo "[global]"; \
            if [ -n "${PIP_INDEX_URL}" ]; then echo "index-url = ${PIP_INDEX_URL}"; fi; \
            if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then echo "extra-index-url = ${PIP_EXTRA_INDEX_URL}"; fi; \
        } > /etc/pip.conf; \
    fi

WORKDIR /skill

RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir pydantic==2.9.0 \
    && apt-get purge -y --auto-remove gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY executor/app/micro_executor /skill/micro_executor
COPY skills/v1-micro-sample/run.py /skill/run.py

ENV PYTHONPATH=/skill

ENTRYPOINT ["python3", "/skill/run.py"]
