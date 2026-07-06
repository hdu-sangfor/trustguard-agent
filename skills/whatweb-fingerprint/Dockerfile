ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG RUBYGEMS_MIRROR_URL
ARG DEBIAN_MIRROR_URL
ARG DEBIAN_SECURITY_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/library/python:3.11-slim
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG RUBYGEMS_MIRROR_URL
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ruby \
    ruby-dev \
    ca-certificates \
    build-essential \
    pkg-config \
    libssl-dev \
    zlib1g-dev \
    libyaml-dev \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -n "${RUBYGEMS_MIRROR_URL}" ]; then \
        gem sources --add "${RUBYGEMS_MIRROR_URL}" --remove https://rubygems.org/; \
    fi
RUN gem install --no-document bundler
RUN if [ -n "${RUBYGEMS_MIRROR_URL}" ]; then \
        bundle config mirror.https://rubygems.org "${RUBYGEMS_MIRROR_URL}"; \
    fi

RUN set -eux; \
    proxy="${TRUSTGUARD_NETWORK_PROXY}"; \
    if [ -n "${proxy}" ] && git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" ls-remote https://github.com/urbanadventurer/WhatWeb.git HEAD >/dev/null 2>&1; then \
        echo "trustguard: cloning WhatWeb via proxy ${proxy}"; \
        git -c "http.proxy=${proxy}" -c "https.proxy=${proxy}" clone --depth=1 https://github.com/urbanadventurer/WhatWeb.git /opt/whatweb \
        || { \
            echo "trustguard: proxy clone failed, falling back to direct network"; \
            rm -rf /opt/whatweb; \
            git clone --depth=1 https://github.com/urbanadventurer/WhatWeb.git /opt/whatweb; \
        }; \
    else \
        if [ -n "${proxy}" ]; then \
            echo "trustguard: proxy configured but not reachable/usable for git, using direct network"; \
        else \
            echo "trustguard: no build proxy configured for git, using direct network"; \
        fi; \
        git clone --depth=1 https://github.com/urbanadventurer/WhatWeb.git /opt/whatweb; \
    fi
RUN ln -s /opt/whatweb/whatweb /usr/local/bin/whatweb

WORKDIR /opt/whatweb
RUN bundle install --jobs 4

WORKDIR /skill
COPY scripts ./scripts

ENTRYPOINT ["python3", "scripts/execute.py"]
