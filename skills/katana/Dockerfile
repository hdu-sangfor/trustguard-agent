# Katana + Dirsearch only (no Nuclei) — pair with dispatcher + nuclei
ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG ALPINE_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/alpine:3.19
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG ALPINE_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY
RUN if [ -n "${PIP_INDEX_URL}" ] || [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
        { \
            echo "[global]"; \
            if [ -n "${PIP_INDEX_URL}" ]; then echo "index-url = ${PIP_INDEX_URL}"; fi; \
            if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then echo "extra-index-url = ${PIP_EXTRA_INDEX_URL}"; fi; \
        } > /etc/pip.conf; \
    fi
RUN if [ -n "${ALPINE_MIRROR_URL}" ]; then \
        alpine_version="$(cut -d. -f1,2 /etc/alpine-release)"; \
        printf '%s\n' "${ALPINE_MIRROR_URL}/v${alpine_version}/main" "${ALPINE_MIRROR_URL}/v${alpine_version}/community" > /etc/apk/repositories; \
    fi

ARG KATANA_VERSION=1.5.0
ARG TRUSTGUARD_PROXY_PROBE_MAX_TIME=10
ARG TRUSTGUARD_PROXY_DOWNLOAD_MAX_TIME=90

RUN apk add --no-cache python3 py3-pip py3-yaml git wget unzip ca-certificates bash libc6-compat gcompat

RUN export ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then \
        KATANA_ARCH="amd64"; \
    elif [ "$ARCH" = "aarch64" ]; then \
        KATANA_ARCH="arm64"; \
    elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then \
        KATANA_ARCH="armv6"; \
    else \
        echo "Unsupported architecture: $ARCH" && exit 1; \
    fi && \
    katana_url="https://github.com/projectdiscovery/katana/releases/download/v${KATANA_VERSION}/katana_${KATANA_VERSION}_linux_${KATANA_ARCH}.zip" && \
    proxy="${TRUSTGUARD_NETWORK_PROXY}" && \
    if [ -n "${proxy}" ] && wget -T "${TRUSTGUARD_PROXY_PROBE_MAX_TIME}" -qO /dev/null -e "use_proxy=yes" -e "http_proxy=${proxy}" -e "https_proxy=${proxy}" https://github.com/; then \
        echo "trustguard: downloading katana via proxy ${proxy}"; \
        wget -T "${TRUSTGUARD_PROXY_DOWNLOAD_MAX_TIME}" -qO /tmp/katana.zip -e "use_proxy=yes" -e "http_proxy=${proxy}" -e "https_proxy=${proxy}" "${katana_url}" \
        || { \
            echo "trustguard: proxy download failed, falling back to direct network"; \
            rm -f /tmp/katana.zip; \
            wget -T 60 -qO /tmp/katana.zip "${katana_url}"; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable, using direct network"; \
        else \
            echo "trustguard: no build proxy configured, using direct network"; \
        fi; \
        wget -T 60 -qO /tmp/katana.zip "${katana_url}"; \
    fi \
    && unzip -q /tmp/katana.zip -d /tmp \
    && mv /tmp/katana /usr/local/bin/katana \
    && chmod +x /usr/local/bin/katana \
    && rm -f /tmp/katana.zip

RUN set -eux; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" ls-remote https://github.com/maurosoria/dirsearch.git HEAD >/dev/null 2>&1; then \
        echo "trustguard: cloning dirsearch via proxy ${proxy}"; \
        git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" clone --depth 1 https://github.com/maurosoria/dirsearch.git /usr/local/dirsearch \
        || { \
            echo "trustguard: proxy clone failed, falling back to direct network"; \
            rm -rf /usr/local/dirsearch; \
            git clone --depth 1 https://github.com/maurosoria/dirsearch.git /usr/local/dirsearch; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable for git, using direct network"; \
        else \
            echo "trustguard: no build proxy configured for git, using direct network"; \
        fi; \
        git clone --depth 1 https://github.com/maurosoria/dirsearch.git /usr/local/dirsearch; \
    fi; \
    python3 -m pip install --no-cache-dir --break-system-packages -r /usr/local/dirsearch/requirements.txt; \
    ln -sf /usr/local/dirsearch/dirsearch.py /usr/local/bin/dirsearch

WORKDIR /skill
COPY web-vuln-common/scripts /skill/common
COPY katana/scripts /skill/scripts
COPY katana/wordlists /skill/wordlists

ENTRYPOINT ["python3", "/skill/scripts/execute.py"]
