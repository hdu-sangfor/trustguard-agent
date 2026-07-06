ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG ALPINE_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/alpine:3.24 AS httpx
ARG ALPINE_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY

ARG HTTPX_VERSION=1.9.0
ARG TARGETARCH=amd64
ARG TRUSTGUARD_PROXY_PROBE_MAX_TIME=10
ARG TRUSTGUARD_PROXY_DOWNLOAD_MAX_TIME=90

RUN if [ -n "${ALPINE_MIRROR_URL}" ]; then \
        alpine_version="$(cut -d. -f1,2 /etc/alpine-release)"; \
        printf '%s\n' "${ALPINE_MIRROR_URL}/v${alpine_version}/main" "${ALPINE_MIRROR_URL}/v${alpine_version}/community" > /etc/apk/repositories; \
    fi
RUN apk add --no-cache ca-certificates curl unzip
WORKDIR /tmp/httpx
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) httpx_arch="amd64"; httpx_sha256="54c6c91d61d3b82ba79f93633df04bb547f0c954d9d9b0fb8bcedf158f85ff2f" ;; \
      arm64) httpx_arch="arm64"; httpx_sha256="40fe2896f0357d281c3edd7a8f2e70ce54972e5082a7a9ea3e59134ca7e15d89" ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    httpx_zip="httpx_${HTTPX_VERSION}_linux_${httpx_arch}.zip"; \
    httpx_url="https://github.com/projectdiscovery/httpx/releases/download/v${HTTPX_VERSION}/${httpx_zip}"; \
    proxy="${TRUSTGUARD_NETWORK_PROXY:-}"; \
    if [ -n "${proxy}" ] && curl --proxy "${proxy}" --connect-timeout 5 --max-time "${TRUSTGUARD_PROXY_PROBE_MAX_TIME}" -fsS https://github.com/ -o /dev/null; then \
      echo "trustguard: downloading httpx via proxy ${proxy}"; \
      curl --proxy "${proxy}" -fsSLO --connect-timeout 10 --max-time "${TRUSTGUARD_PROXY_DOWNLOAD_MAX_TIME}" "${httpx_url}" \
      || { \
        echo "trustguard: proxy download failed, falling back to direct network"; \
        rm -f "${httpx_zip}"; \
        curl -fsSLO --connect-timeout 30 --max-time 300 "${httpx_url}"; \
      }; \
    else \
      if [ -n "${proxy}" ]; then \
        echo "trustguard: proxy configured but not reachable/usable, using direct network"; \
      else \
        echo "trustguard: no build proxy configured, using direct network"; \
      fi; \
      curl -fsSLO --connect-timeout 30 --max-time 300 "${httpx_url}"; \
    fi; \
    echo "${httpx_sha256}  ${httpx_zip}" | sha256sum -c -; \
    unzip "${httpx_zip}"; \
    install -m 0755 httpx /usr/local/bin/httpx

FROM ${DOCKERHUB_REGISTRY}/library/python:3.13-alpine
ARG ALPINE_MIRROR_URL
RUN if [ -n "${ALPINE_MIRROR_URL}" ]; then \
        alpine_version="$(cut -d. -f1,2 /etc/alpine-release)"; \
        printf '%s\n' "${ALPINE_MIRROR_URL}/v${alpine_version}/main" "${ALPINE_MIRROR_URL}/v${alpine_version}/community" > /etc/apk/repositories; \
    fi

RUN apk add --no-cache ca-certificates

COPY --from=httpx /usr/local/bin/httpx /usr/local/bin/httpx

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
