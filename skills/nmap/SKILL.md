---
name: nmap
description: Network discovery and service detection. Scans hosts for open ports, service versions, and basic scripts (e.g. -sV -sC). Use for reconnaissance and mapping target attack surface.
metadata: { "runtime": { "emoji": "🔌", "requires": { "bins": ["nmap"], "env": [] }, "primaryEnv": null } }
---

# Nmap

Network scanner for port discovery and service/version detection.

## Usage

Used by the executor with `target` (host or IP) and optional `params.ports`, `params.top_ports`, `params.scripts`, `params.extra` (nmap args), `params.timeout`.

## Parameters

| Param   | Type   | Required | Description                    |
|---------|--------|----------|--------------------------------|
| target  | string | yes      | Host or IP to scan             |
| ports   | string | no       | Port list, e.g. "22,80,443"    |
| top_ports | int/string | no | Nmap top ports count (`--top-ports`) |
| scripts | string/list | no | Nmap NSE script or list (`--script`) |
| extra   | list   | no       | Extra nmap CLI arguments       |
| timeout | int    | no       | Timeout in seconds (default 120) |

## Docker

Image built from `skills/nmap/Dockerfile` via `docker compose --profile skills build skill-nmap`.
