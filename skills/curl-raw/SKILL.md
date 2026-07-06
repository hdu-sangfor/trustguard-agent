---
name: curl-raw
description: Raw HTTP probing with curl. Use for custom method, headers and body requests in recon and validation.
metadata: { "runtime": { "emoji": "🧪", "requires": { "bins": ["curl"], "env": [] }, "primaryEnv": null } }
---

# Curl Raw

Run raw HTTP requests with `curl`.

## Parameters

- `url` or `target`: required
- `method`: optional, default `GET`
- `headers`: optional dict, like `{"User-Agent":"x"}`
- `data`: optional request body
- `follow_redirects`: optional bool, default true
- `timeout`: optional seconds, default 20
- `auto_urlencode_path`: optional bool, default true。会自动编码 URL 路径中的高风险特殊字符（如 `{}` `[]` 空格），减少中间件 RFC 3986 拒绝导致的 400
