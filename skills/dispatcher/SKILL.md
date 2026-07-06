---
name: dispatcher
description: ETL + path-template dedupe + chunking + manifest. operation=prepare (default) or finalize. Context (Cookie/UA/tags) stored in manifest for nuclei.
metadata: { "runtime": { "emoji": "📦", "requires": { "bins": [], "env": [] }, "primaryEnv": null } }
---

# Dispatcher（ETL / 分片）

## prepare

Reads `discovery/katana_urls.txt` and `discovery/dirsearch.json` from the run directory, runs ETL (`etl-4`), template dedupe, scores endpoints, writes `chunks/chunk_*.txt` and `manifest.json`.

**Required:** `run_id` (from katana), `target` for scope.

## finalize

Merges `results/chunk_*.jsonl` from completed chunks into `vulnerabilities` and `severity_histogram`. `pending_chunk_indices` lists chunks not marked `done` (partial run).

## Parameters

| Param | Description |
|-------|-------------|
| `operation` | `prepare` (default) or `finalize` |
| `run_id` | Required |
| `chunk_size` | Default 20 |
| `max_urls` | Cap after ETL (default 500) |
| `auth_header` / `user_agent` / `tags` | Stored in manifest for Nuclei |
| `manifest_ttl_seconds` | TTL hint per chunk (default 3600) |

## Determinism

`determinism_version` is **etl-4** (matrix strip + template dedupe).

## Docker

Build: `trustguard-skill-dispatcher:latest` via `docker compose --profile skills build skill-dispatcher`.
