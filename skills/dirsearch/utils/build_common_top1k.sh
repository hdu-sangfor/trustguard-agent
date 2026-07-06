#!/usr/bin/env bash
set -euo pipefail

# Run from repository root:
#   ./skills/dirsearch/utils/build_common_top1k.sh

SOURCE="skills/dirsearch/wordlists/v1/onelistforallshort.txt"
OUTPUT="skills/dirsearch/wordlists/v1/core/common_top1k.txt"

if [[ ! -f "$SOURCE" ]]; then
  echo "[x] source not found: $SOURCE"
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

# 1) Frequency baseline (Top 850)
head -n 850 "$SOURCE" > "$OUTPUT"

# 2) Critical leakage + probing seeds
cat <<'EOF' >> "$OUTPUT"
/.env
/.git/config
/.svn/entries
/.htaccess
/web.config
/.DS_Store
/robots.txt
/sitemap.xml
/.well-known/security.txt
/config.php.bak
/config.save
/.bash_history
/WEB-INF/web.xml
/META-INF/MANIFEST.MF
/info.php
/phpinfo.php
/pmainfo.php
/package.json
/tsconfig.json
/.npmrc
/Dockerfile
/docker-compose.yml
/.dockerignore
EOF

# 3) Stable de-dup (keep first occurrence order)
awk '!seen[$0]++' "$OUTPUT" > "${OUTPUT}.tmp" && mv "${OUTPUT}.tmp" "$OUTPUT"

# 4) Hard cap to 1000 lines
head -n 1000 "$OUTPUT" > "${OUTPUT}.final" && mv "${OUTPUT}.final" "$OUTPUT"

echo "[+] common_top1k built: $OUTPUT"
echo "[+] total lines: $(wc -l < "$OUTPUT")"
