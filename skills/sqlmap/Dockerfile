# Sqlmap skill — 工具安装镜像
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
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# 使用官方仓库安装 sqlmap（可改为 pip install sqlmap 若 PyPI 可用）
RUN set -eux; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" ls-remote https://github.com/sqlmapproject/sqlmap.git HEAD >/dev/null 2>&1; then \
        echo "trustguard: cloning sqlmap via proxy ${proxy}"; \
        git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap \
        || { \
            echo "trustguard: proxy clone failed, falling back to direct network"; \
            rm -rf /opt/sqlmap; \
            git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable for git, using direct network"; \
        else \
            echo "trustguard: no build proxy configured for git, using direct network"; \
        fi; \
        git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /opt/sqlmap; \
    fi
WORKDIR /skill
COPY scripts ./scripts
RUN pip install --no-cache-dir pyyaml

ENTRYPOINT ["python3", "scripts/execute.py"]
