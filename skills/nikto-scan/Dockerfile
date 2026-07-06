ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
ARG TRUSTGUARD_NETWORK_PROXY

RUN if [ -n "${DEBIAN_MIRROR_URL}" ]; then \
        sed -i "s|http://deb.debian.org/debian|${DEBIAN_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -n "${DEBIAN_SECURITY_MIRROR_URL}" ]; then \
        sed -i "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR_URL}|g" /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends git perl ca-certificates libjson-perl libxml-writer-perl && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" ls-remote https://github.com/sullo/nikto.git HEAD >/dev/null 2>&1; then \
        echo "trustguard: cloning nikto via proxy ${proxy}"; \
        git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" clone --depth=1 https://github.com/sullo/nikto.git /opt/nikto \
        || { \
            echo "trustguard: proxy clone failed, falling back to direct network"; \
            rm -rf /opt/nikto; \
            git clone --depth=1 https://github.com/sullo/nikto.git /opt/nikto; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable for git, using direct network"; \
        else \
            echo "trustguard: no build proxy configured for git, using direct network"; \
        fi; \
        git clone --depth=1 https://github.com/sullo/nikto.git /opt/nikto; \
    fi; \
    ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
