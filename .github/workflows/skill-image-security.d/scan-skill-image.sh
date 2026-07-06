#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <skill-security-config.env>" >&2
  exit 2
fi

config_file="$1"
if [ ! -f "${config_file}" ]; then
  echo "missing skill security config: ${config_file}" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "${config_file}"

: "${SKILL_ID:?missing SKILL_ID}"
: "${IMAGE_TAG:?missing IMAGE_TAG}"
: "${DOCKERFILE:?missing DOCKERFILE}"
: "${BUILD_CONTEXT:?missing BUILD_CONTEXT}"

artifact_dir="${ARTIFACT_DIR:-ci-artifacts}"
mkdir -p "${artifact_dir}"

sbom_path="${artifact_dir}/sbom-skill-${SKILL_ID}.spdx.json"
grype_json_path="${artifact_dir}/grype-skill-${SKILL_ID}.json"
grype_table_path="${artifact_dir}/grype-skill-${SKILL_ID}.txt"

docker build -t "${IMAGE_TAG}" -f "${DOCKERFILE}" "${BUILD_CONTEXT}"

syft scan "docker:${IMAGE_TAG}" -o "spdx-json=${sbom_path}"
test -s "${sbom_path}"

grype "sbom:${sbom_path}" \
  --only-fixed \
  --output json > "${grype_json_path}"

grype "sbom:${sbom_path}" \
  --only-fixed \
  --output table | tee "${grype_table_path}"

SKILL_ID="${SKILL_ID}" GRYPE_JSON_PATH="${grype_json_path}" python - <<'PY'
import json
import os
from collections import Counter

skill_id = os.environ["SKILL_ID"]
grype_json_path = os.environ["GRYPE_JSON_PATH"]

with open(grype_json_path, encoding="utf-8") as fh:
    matches = json.load(fh).get("matches", [])

counts = Counter(
    str(match.get("vulnerability", {}).get("severity", "unknown")).lower()
    for match in matches
)
high_or_critical = counts["high"] + counts["critical"]
print(
    "Grype fixed-vulnerability counts: "
    + ", ".join(f"{severity}={count}" for severity, count in sorted(counts.items()))
)
if high_or_critical:
    print(
        f"::warning::Grype found {high_or_critical} fixed high/critical "
        f"vulnerabilities in the {skill_id} skill image. See uploaded grype reports."
    )
PY
