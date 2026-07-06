ARG DOCKERHUB_REGISTRY=docker.io
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
FROM ${DOCKERHUB_REGISTRY}/metasploitframework/metasploit-framework:latest
ARG PIP_INDEX_URL
ARG PIP_EXTRA_INDEX_URL
RUN if [ -n "${PIP_INDEX_URL}" ] || [ -n "${PIP_EXTRA_INDEX_URL}" ]; then \
        { \
            echo "[global]"; \
            if [ -n "${PIP_INDEX_URL}" ]; then echo "index-url = ${PIP_INDEX_URL}"; fi; \
            if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then echo "extra-index-url = ${PIP_EXTRA_INDEX_URL}"; fi; \
        } > /etc/pip.conf; \
    fi

RUN pip3 install --no-cache-dir pyyaml

WORKDIR /skill
COPY scripts ./scripts

ENV PATH="/usr/src/metasploit-framework:${PATH}"

ENTRYPOINT ["python3", "scripts/execute.py"]
