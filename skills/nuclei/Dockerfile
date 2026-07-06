ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.12-slim
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY

# 1. 安装基础依赖
RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip procps ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

# 2. 安装 Nuclei 二进制
ARG NUCLEI_VERSION=3.3.5
ARG NUCLEI_DOWNLOAD_BASE_URL=https://github.com/projectdiscovery/nuclei/releases/download
ARG NUCLEI_DOWNLOAD_CONNECT_TIMEOUT=60
ARG NUCLEI_DOWNLOAD_MAX_TIME=300
ARG NUCLEI_PROXY_PROBE_MAX_TIME=10
ARG NUCLEI_PROXY_DOWNLOAD_MAX_TIME=90
RUN set -eux; \
    nuclei_zip="nuclei_${NUCLEI_VERSION}_linux_amd64.zip"; \
    nuclei_url="${NUCLEI_DOWNLOAD_BASE_URL}/v${NUCLEI_VERSION}/${nuclei_zip}"; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    curl_common="-L --fail --retry 2 --retry-all-errors --retry-delay 2"; \
    if [ -n "${proxy}" ] && curl --proxy "${proxy}" --connect-timeout 5 --max-time "${NUCLEI_PROXY_PROBE_MAX_TIME}" -fsS https://github.com/ -o /dev/null; then \
      echo "trustguard: downloading nuclei via proxy ${proxy}"; \
      curl --proxy "${proxy}" ${curl_common} \
        --connect-timeout 10 \
        --max-time "${NUCLEI_PROXY_DOWNLOAD_MAX_TIME}" \
        "${nuclei_url}" -o nuclei.zip \
      || { \
        echo "trustguard: proxy download failed, falling back to direct network"; \
        rm -f nuclei.zip; \
        curl ${curl_common} \
          --connect-timeout "${NUCLEI_DOWNLOAD_CONNECT_TIMEOUT}" \
          --max-time "${NUCLEI_DOWNLOAD_MAX_TIME}" \
          "${nuclei_url}" -o nuclei.zip; \
      }; \
    else \
      if [ -n "${proxy}" ]; then \
        echo "trustguard: proxy configured but not reachable/usable, using direct network"; \
      else \
        echo "trustguard: no build proxy configured, using direct network"; \
      fi; \
      curl ${curl_common} \
        --connect-timeout "${NUCLEI_DOWNLOAD_CONNECT_TIMEOUT}" \
        --max-time "${NUCLEI_DOWNLOAD_MAX_TIME}" \
        "${nuclei_url}" -o nuclei.zip; \
    fi; \
    unzip nuclei.zip; \
    mv nuclei /usr/local/bin/; \
    rm nuclei.zip

# 3. 仅使用 skills/nuclei/temlists 作为模板库（不内置 projectdiscovery/nuclei-templates 全量库）
WORKDIR /skill
COPY nuclei/scripts /skill/scripts
COPY nuclei/kb_features.py /skill/kb_features.py
COPY nuclei/common /skill/common
COPY nuclei/temlists /skill/temlists

# Nuclei 全局模板目录与 -t 默认解析：仅指向 temlists
ENV NUCLEI_TEMPLATES_DIR=/skill/temlists

RUN chmod +x /usr/local/bin/nuclei
ENTRYPOINT ["python3", "/skill/scripts/execute.py"]
