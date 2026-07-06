#!/usr/bin/env bash
set -euo pipefail

SYFT_VERSION="${SYFT_VERSION:-1.45.1}"
GRYPE_VERSION="${GRYPE_VERSION:-0.114.0}"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

curl -sSfL "https://raw.githubusercontent.com/anchore/syft/main/install.sh" \
  | sh -s -- -b "${INSTALL_DIR}" "v${SYFT_VERSION}"

curl -sSfL "https://raw.githubusercontent.com/anchore/grype/main/install.sh" \
  | sh -s -- -b "${INSTALL_DIR}" "v${GRYPE_VERSION}"
