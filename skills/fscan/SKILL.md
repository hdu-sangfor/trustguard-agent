---
name: fscan
description: Comprehensive network security scanner. Performs port scanning, service detection, vulnerability discovery, and asset enumeration. Use for quick reconnaissance and vulnerability assessment.
metadata: { "runtime": { "emoji": "🔍", "requires": { "bins": ["fscan"], "env": [] }, "primaryEnv": null } }
---

# Fscan

Comprehensive network security scanner for asset discovery and vulnerability detection.

## Usage

Used by the executor with `target` (host or IP) and optional `params.ports`, `params.top_ports`, `params.modules`, `params.extra` (fscan args), `params.timeout`.

## Parameters

| Param   | Type   | Required | Description                    |
|---------|--------|----------|--------------------------------|
| target  | string | yes      | Host or IP to scan             |
| ports   | string | no       | Port list, e.g. "22,80,443"    |
| top_ports | int/string | no | Scan top N ports               |
| modules | string/list | no | Scan modules (e.g., "port,services") |
| extra   | list   | no       | Extra fscan CLI arguments      |
| timeout | int    | no       | Timeout in seconds (default 300) |

## Docker

Image built from `skills/fscan/Dockerfile`.
