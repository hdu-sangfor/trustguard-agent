---
name: ehole
description: Web fingerprint reconnaissance using EHole. Use for lightweight CMS/framework identification during recon.
metadata: { "runtime": { "emoji": "🕳️", "requires": { "bins": ["ehole"], "env": [] }, "primaryEnv": null } }
---

# EHole

Run EHole fingerprint scan for a single URL target and extract concise identification facts.

## Default command

`ehole finger -u {target_url}`

## Output fields (parsed_artifacts)

- `input_url`
- `fingerprints` (best effort extracted lines)
- `command`
- `raw_preview`
