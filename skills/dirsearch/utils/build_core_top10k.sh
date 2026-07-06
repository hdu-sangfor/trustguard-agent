#!/usr/bin/env bash
set -euo pipefail

# Build from repository root:
#   ./skills/dirsearch/utils/build_core_top10k.sh

TMP_DIR="${TMPDIR:-/tmp}/wordlist_build"
OUT_DIR="skills/dirsearch/wordlists/v1/base"
OUT_FILE="${OUT_DIR}/core_top10k.txt"

mkdir -p "$OUT_DIR"
python3 "skills/dirsearch/utils/build_core_top10k.py" --output "$OUT_FILE" --tmp-dir "$TMP_DIR"
