---
name: baidu-search
description: Search the web using Baidu AI Search Engine (BDSE). Use for live information, documentation, or research topics.
metadata:
  runtime:
    emoji: "🔍"
    requires:
      bins:
        - python3
      env:
        - BAIDU_API_KEY
    primaryEnv: BAIDU_API_KEY
---

# Baidu Search

Search the web via Baidu AI Search API.

## Usage

```bash
python3 scripts/search.py '<JSON>'
```

## Request Parameters

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| query | str | yes | - | Search query |
| edition | str | no | standard | `standard` (full) or `lite` (light) |
| resource_type_filter | list[obj] | no | web:20, others:0 | Resource types |
| search_filter | obj | no | - | Advanced filters |
| block_websites | list[str] | no | - | Sites to block |
| search_recency_filter | str | no | - | Time filter: `week`, `month`, `semiyear`, `year` |
| safe_search | bool | no | false | Enable strict content filtering |

## Docker

Image built from `skills/baidu-search/Dockerfile` via `docker compose --profile skills build skill-baidu-search`. Requires `BAIDU_API_KEY` at runtime.
