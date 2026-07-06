---
name: nikto-scan
description: Web vulnerability scanning using Nikto. Use in VULN_SCAN for common web misconfigurations and known issues.
metadata: { "runtime": { "emoji": "🛡️", "requires": { "bins": ["nikto"], "env": [] }, "primaryEnv": null } }
---

# Nikto Scan

Run Nikto against web targets and return concise findings.

## Parameters

- `url` or `target`: required
- `timeout`: optional seconds, default 300
- `extra`: optional string/list for additional Nikto flags
