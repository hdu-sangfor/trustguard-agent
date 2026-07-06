---
name: httpx
description: Fast HTTP probing and lightweight fingerprinting using ProjectDiscovery httpx. Use for web recon with redirect-awareness and JSON output.
metadata: { "runtime": { "emoji": "🌐", "requires": { "bins": ["httpx"], "env": [] }, "primaryEnv": null } }
---

# HTTPX

Probe a target URL with ProjectDiscovery `httpx` and extract actionable web fingerprint facts.

## Default command

`httpx -u {target_url} -td -sc -title -server -fr -maxr 2 -json -silent -t {threads} -rl {rate_limit}`

## Output fields (parsed_artifacts)

- `input_url`
- `final_url`
- `status_code`
- `title`
- `webserver`
- `tech_stack`
- `redirect_warning` (cross-host redirect detected)
- `out_of_scope_redirect` (same as redirect_warning for policy checks)
