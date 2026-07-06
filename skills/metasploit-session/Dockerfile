ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG ALPINE_MIRROR_URL
FROM ${DOCKERHUB_REGISTRY}/metasploitframework/metasploit-framework:latest
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
ARG ALPINE_MIRROR_URL
RUN if [ -n "${PIP_INDEX_URL}" ] || [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
        { \
            echo "[global]"; \
            if [ -n "${PIP_INDEX_URL}" ]; then echo "index-url = ${PIP_INDEX_URL}"; fi; \
            if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then echo "extra-index-url = ${PIP_EXTRA_INDEX_URL}"; fi; \
        } > /etc/pip.conf; \
    fi

# 覆盖 entrypoint，避免运行 Metasploit 初始化脚本
ENTRYPOINT []

# 安装必要的工具
RUN if [ -n "${ALPINE_MIRROR_URL}" ]; then \
        alpine_version="$(cut -d. -f1,2 /etc/alpine-release)"; \
        printf '%s\n' "${ALPINE_MIRROR_URL}/v${alpine_version}/main" "${ALPINE_MIRROR_URL}/v${alpine_version}/community" > /etc/apk/repositories; \
    fi
RUN apk update && apk add --no-cache \
    python3 \
    py3-pip \
    curl \
    net-tools \
    vim \
    wget \
    && rm -rf /var/cache/apk/*

# 安装 Python 依赖
RUN pip3 install pexpect pyyaml

# 将 Metasploit 添加到 PATH
ENV PATH="/usr/src/metasploit-framework:$PATH"

# 复制执行脚本
COPY scripts/ /skill/scripts/
WORKDIR /skill/scripts

# 设置执行权限
RUN chmod +x execute.py

# 设置默认命令
CMD ["python3", "execute.py"]
