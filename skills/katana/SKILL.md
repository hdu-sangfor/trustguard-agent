---
name: katana
description: Katana crawl (+ optional Dirsearch). Writes discovery/katana_urls.txt under WORKSPACE_ROOT/{task_id}/web-vuln/{run_id}/. Default depth=2, `-ct` crawl cap, `-ef` static extensions filter; pair with dispatcher → nuclei.
metadata: { "runtime": { "emoji": "🕸️", "requires": { "bins": ["katana", "dirsearch"], "env": [] }, "primaryEnv": null } }
---

# Katana（资产发现）

## Flow

1. Run this skill → get `run_id` and paths in `parsed_artifacts`.
2. Run **dispatcher** with `operation=prepare` (or default) and the same `run_id`.
3. Run **nuclei** per chunk (`chunk_index` 1..N), then **dispatcher** with `operation=finalize`.

## Parameters

| Param | Description |
|-------|-------------|
| `target_url` / `url` / `target` | Required seed URL |
| `run_id` | Optional; generated if omitted (pass to dispatcher/nuclei) |
| `timeout` | Total seconds (default 600) |
| `katana_depth` | Default **2** |
| `katana_crawl_duration` | Default **60s** (`-ct`，单目标爬取时长上限) |
| `katana_exclude_extensions` | 默认一组静态后缀，经 `-ef` 从输出中过滤 |
| `katana_concurrency` | Default **40** |
| `katana_form_extraction` | Default true (`-fx -j`) |
| `skip_dirsearch` | Skip Dirsearch phase（默认跳过内置 dirsearch） |
| `headless_js` / `frontend_framework` | If Vue/React/Angular or `KATANA_JS=1`, adds `-jc` for JS render |
| `auth_header` | Passed to Katana `-H` |
| `risk_profile` | `waf_suspected` lowers concurrency |

## Workspace

Outputs live under `{WORKSPACE_ROOT}/{task_id}/web-vuln/{run_id}/discovery/`.

## Optional MQ scale-out

Horizontal scaling with a dedicated `discovery_queue` is **not** wired in the executor by default. Use the platform `execute_tasks` queue + `mq-worker`, or add a dedicated consumer that unmarshals the same JSON payload as HTTP execution. Use `prefetch_count=1` on the consumer to avoid heartbeat loss on long crawls.

## Docker

Build: `trustguard-skill-katana:latest` via `docker compose --profile skills build skill-katana`.
