---
name: dirsearch
description: Advanced web path scanner using multithreaded brute-force. Use for discovering hidden files, directories, and endpoints on web servers.
metadata: { "runtime": { "emoji": "📁", "requires": { "bins": ["dirsearch"], "env": [] }, "primaryEnv": null } }
---

# Dirsearch

Advanced web path scanner for discovering hidden files and directories.

## Local Dict Build (V1)

- Build `core_top10k.txt` from OneListForAll + SecLists:
  - `./skills/dirsearch/utils/build_core_top10k.sh`
- Output:
  - `skills/dirsearch/wordlists/v1/base/core_top10k.txt`

## Strategy

- Semantic scheduling (V1): model passes `keywords` (e.g. `["java","api"]`) and optional `exclude_techs`
- Runtime merges dictionary shards from `wordlists/v1/registry.json`
- Load priority: `seeds > private > base > open mappings`
- Throughput is fixed in runtime, not model-controlled: `-t 100 --max-rate 500`
- Process timeout is fixed to 300s (HTTP timeout still 3s/request)
- Never pass raw dirsearch flags via `extra`; this skill intentionally blocks unsafe free-form flags

## Parameters

- `url` or `target`: required, like `http://example.com`
- `extensions`: optional `string | string[]`, for example `["jsp","jspx","do","action"]`
- `keywords`: optional `string[]`, semantic ammo selector (e.g. `["java","api","admin"]`)
- `exclude_techs`: optional `string[]`, negative filter (e.g. `["php","asp"]`)
- `wordlist_tags`: optional legacy field, runtime will map old tags to semantic keywords for compatibility
- `enable_recursion`: optional `boolean`
  - when `true`, executor forces `-r -R 2 --recursion-status 200,301,302,403`
  - deep recursion and force recursion are intentionally disallowed
- `fuzz_prefixes` / `fuzz_suffixes`: ignored in V1 runtime
- `rate_limit`: ignored by runtime (kept only for backward-compatible payload schema)

## Notes

- Semantic registry is `skills/dirsearch/wordlists/v1/registry.json`
- Runtime reads only deterministic file paths under `/skill/wordlists/v1` (no glob)
- If keywords are empty, runtime defaults to `["java","api"]`
- JSON output format is automatically parsed and structured
- Quiet mode (`-q`) is enabled by default
- Forced hardening flags:
  - `--timeout 3 --retries 1`
  - subprocess hard timeout: `300s`
  - `--random-agent`
  - throughput: `-t 100 --max-rate 500`
  - `--exclude-subdirs image,images,css,js,style,static,font,fonts`
  - `enable_recursion=true` => `-r -R 2 --recursion-status 200,301,302,403`
