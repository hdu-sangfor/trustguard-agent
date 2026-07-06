---
name: nuclei
description: Nuclei scan using only `temlists/` as the template root (no bundled projectdiscovery/nuclei-templates); framework-routed matrix and phase-isolated tags (`safe-poc` / `exploit`).
metadata: { "runtime": { "emoji": "☢️", "requires": { "bins": ["nuclei"], "env": [] }, "primaryEnv": null } }
---

# Nuclei（分片漏洞扫描）

## Safety

- **Will not** run Nuclei without a non-empty `-l` file of valid `http(s)://` URLs.
- Templates are physically routed by `framework_hint` into `temlists/<framework>/`.
- VULN_SCAN defaults to `-tags safe-poc`, EXPLOIT defaults to `-tags exploit`.
- No full-site unscoped template sweep.

## Parameters

| Param | Description |
|-------|-------------|
| `run_id` | Required (same as pipeline) |
| `chunk_index` | 1-based index matching manifest |
| `chunk_path` | Alternative: manifest path e.g. `chunks/chunk_0001.txt` |
| `timeout` | Per-chunk seconds (default 60; keep 20–40 typical) |
| `rate_limit` | Nuclei `-rl`。建议传正整数；兼容 `low/medium/high`（会映射为 10/30/80），默认 50 |
| `tags` / `nuclei_tags` | Override manifest tags |
| `framework_hint` | Framework namespace hint: `struts2`, `spring`, `thinkphp`, `jenkins`, `solr`, `elasticsearch`, `weblogic`. Use the detected technology exactly. |
| `mode` | `scan` or `exploit` (drives tag isolation) |
| `output_discovery_dir` | Shared discovery dir, prefer `clustered_targets.txt` as list input |

## Results

Writes `results/chunk_NNNN.jsonl` and sets manifest chunk `status` to `done` or `failed`.

## Docker

Build: `trustguard-skill-nuclei:latest` via `docker compose --profile skills build skill-nuclei`. Image copies **only** `temlists/`; `NUCLEI_TEMPLATES_DIR=/skill/temlists`. No `nuclei -update-templates` / full community template tree in this skill.
