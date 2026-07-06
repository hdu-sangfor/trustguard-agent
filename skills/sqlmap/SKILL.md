---
name: sqlmap
description: Automated SQL injection detection and exploitation. Tests web URLs for SQLi with configurable risk/level. Use in exploit phase for validated targets.
metadata: { "runtime": { "emoji": "💉", "requires": { "bins": ["python3"], "env": [] }, "primaryEnv": null } }
---

# Sqlmap

SQL injection tool for detecting and exploiting SQL injection flaws.

## Usage

Executor invokes with `target` (URL) and optional `params.extra` (sqlmap CLI args), `params.timeout`.

## Parameters

| Param   | Type   | Required | Description                    |
|---------|--------|----------|--------------------------------|
| target  | string | yes      | URL to test (e.g. with ?id=1)  |
| extra   | list   | no       | Extra sqlmap CLI arguments     |
| timeout | int    | no       | Timeout in seconds (default 600) |

## Docker

Image built from `skills/sqlmap/Dockerfile`.
