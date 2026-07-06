# Fscan skill — worker 镜像，入口为 scripts/execute.py
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
ARG TRUSTGUARD_NETWORK_PROXY
RUN if [ -n "${PIP_INDEX_URL}" ] || [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
        { \
            echo "[global]"; \
            if [ -n "${PIP_INDEX_URL}" ]; then echo "index-url = ${PIP_INDEX_URL}"; fi; \
            if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then echo "extra-index-url = ${PIP_EXTRA_INDEX_URL}"; fi; \
        } > /etc/pip.conf; \
    fi

RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN set -eux; \
    fscan_url="https://github.com/shadow1ng/fscan/releases/download/1.8.4/fscan"; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && wget -T 10 -qO /dev/null -e "use_proxy=yes" -e "http_proxy=${proxy}" -e "https_proxy=${proxy}" https://github.com/; then \
        echo "trustguard: downloading fscan via proxy ${proxy}"; \
        wget -T 90 -O fscan -e "use_proxy=yes" -e "http_proxy=${proxy}" -e "https_proxy=${proxy}" "${fscan_url}" \
        || { \
            echo "trustguard: proxy download failed, falling back to direct network"; \
            rm -f fscan; \
            wget -T 60 -O fscan "${fscan_url}"; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable, using direct network"; \
        else \
            echo "trustguard: no build proxy configured, using direct network"; \
        fi; \
        wget -T 60 -O fscan "${fscan_url}"; \
    fi; \
    chmod +x fscan; \
    mv fscan /usr/local/bin/fscan

WORKDIR /skill
COPY scripts ./scripts
RUN pip install --no-cache-dir pyyaml

ENTRYPOINT ["python3", "scripts/execute.py"]
