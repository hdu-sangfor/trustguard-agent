---
name: ffuf-dir-enum
description: Directory and endpoint brute-force using ffuf. Use for web content discovery after basic fingerprinting.
metadata: { "runtime": { "emoji": "📂", "requires": { "bins": ["ffuf"], "env": [] }, "primaryEnv": null } }
---

# FFUF Dir Enum

Enumerate web paths with `ffuf`.

## Parameters

- `url` or `target`: required, like `http://host/FUZZ`
- `wordlist`: optional local path
- `threads`: optional int, default 20
- `mc`: optional match status, default `200,204,301,302,307,401,403`
- `timeout`: optional seconds, default 120
