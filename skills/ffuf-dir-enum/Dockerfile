ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG GOPROXY
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG GOPROXY
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY

RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends golang-go ca-certificates && rm -rf /var/lib/apt/lists/* \
    && set -eu; \
    go_proxy="${GOPROXY:-}"; \
    proxy="${TRUSTGUARD_NETWORK_PROXY:-}"; \
    if [ -n "${go_proxy}" ]; then \
        case ",${go_proxy}," in \
            *,direct,*) ;; \
            *) go_proxy="${go_proxy},direct" ;; \
        esac; \
        if [ -n "${proxy}" ]; then echo "trustguard: go install ffuf with proxy ${proxy}"; fi; \
        HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" GOPROXY="${go_proxy}" GOBIN=/usr/local/bin go install github.com/ffuf/ffuf/v2@latest; \
    else \
        if [ -n "${proxy}" ]; then echo "trustguard: go install ffuf with proxy ${proxy}"; fi; \
        HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" GOBIN=/usr/local/bin go install github.com/ffuf/ffuf/v2@latest; \
    fi

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
