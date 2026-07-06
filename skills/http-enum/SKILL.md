---
name: http-enum
description: Basic HTTP fingerprint and header enumeration using curl. Use for web recon when only lightweight tooling is available.
metadata: { "runtime": { "emoji": "🌐", "requires": { "bins": ["curl"], "env": [] }, "primaryEnv": null } }
---

# HTTP Enum

Enumerate web response headers, status code, redirects and server banner with `curl`.

## Usage

The executor runs `scripts/execute.py` with JSON payload and returns parsed artifacts:
- `url`
- `http_status`
- `headers`
- `title` (best effort)
