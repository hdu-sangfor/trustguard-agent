ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG GOPROXY
ARG ALPINE_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/golang:1.24-alpine AS builder
ARG GOPROXY
ARG ALPINE_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY

RUN if [ -n "${ALPINE_MIRROR_URL}" ]; then \
        alpine_version="$(cut -d. -f1,2 /etc/alpine-release)"; \
        printf '%s\n' "${ALPINE_MIRROR_URL}/v${alpine_version}/main" "${ALPINE_MIRROR_URL}/v${alpine_version}/community" > /etc/apk/repositories; \
    fi
RUN apk add --no-cache git
WORKDIR /src
RUN set -eux; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" ls-remote https://github.com/EdgeSecurityTeam/EHole.git HEAD >/dev/null 2>&1; then \
        echo "trustguard: cloning EHole via proxy ${proxy}"; \
        git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" clone --depth=1 https://github.com/EdgeSecurityTeam/EHole.git . \
        || { \
            echo "trustguard: proxy clone failed, falling back to direct network"; \
            rm -rf .git * .[^.]*; \
            git clone --depth=1 https://github.com/EdgeSecurityTeam/EHole.git .; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable for git, using direct network"; \
        else \
            echo "trustguard: no build proxy configured for git, using direct network"; \
        fi; \
        git clone --depth=1 https://github.com/EdgeSecurityTeam/EHole.git .; \
    fi
RUN set -eu; \
    go_proxy="${GOPROXY:-}"; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${go_proxy}" ]; then \
        case ",${go_proxy}," in \
            *,direct,*) ;; \
            *) go_proxy="${go_proxy},direct" ;; \
        esac; \
        HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" GOPROXY="${go_proxy}" go build -o /go/bin/ehole .; \
    else \
        HTTP_PROXY="${proxy}" HTTPS_PROXY="${proxy}" go build -o /go/bin/ehole .; \
    fi

FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=builder /go/bin/ehole /usr/local/bin/ehole
# EHole 在运行时用 GetCurrentAbPathByExecutable() + "/finger.json" 加载规则库，必须与二进制同目录
COPY --from=builder /src/finger.json /usr/local/bin/finger.json

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
