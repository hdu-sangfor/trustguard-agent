---
name: fenjing
description: Automated Jinja2 SSTI exploitation tool for bypassing WAF and detecting template injection vulnerabilities. Designed for CTF competitions with automatic parameter fuzzing and payload generation.
metadata: { "runtime": { "emoji": "🔥", "requires": { "bins": ["python"], "env": [] }, "primaryEnv": null } }
---

# Fenjing

Automated Jinja2 SSTI (Server-Side Template Injection) exploitation tool for bypassing WAF and detecting template injection vulnerabilities.

## Parameters

- `url` or `target`: required, like `http://example.com`
- `mode`: optional, default `scan`, supported modes:
  - `scan`: automatically scan and fuzz parameters
  - `crack`: attack specific parameters with GET/POST method
  - `crack-path`: attack URL path-based SSTI
  - `crack-json`: attack JSON API endpoints
  - `crack-request`: attack from HTTP request file
- `inputs`: optional, comma-separated parameter names (for crack/crack-path modes)
- `method`: optional, HTTP method (default `GET`, for crack mode)
- `json_data`: optional, JSON string containing request body (for crack-json mode)
- `key`: optional, JSON key to inject payload into (for crack-json mode)
- `request_file`: optional, path to HTTP request file (for crack-request mode)
- `host`: optional, target host (for crack-request mode)
- `port`: optional, target port (default `80`, for crack-request mode)
- `timeout`: optional seconds, default 600
- `extra`: optional string/list for additional fenjing flags

## Examples

```json
{
  "params": {
    "url": "http://example.com",
    "mode": "scan"
  }
}
```

```json
{
  "params": {
    "url": "http://example.com/hello",
    "mode": "crack",
    "inputs": "name,message",
    "method": "GET"
  }
}
```

```json
{
  "params": {
    "url": "http://example.com/api/endpoint",
    "mode": "crack-json",
    "json_data": "{\"name\": \"admin\", \"msg\": \"\"}",
    "key": "msg"
  }
}
```