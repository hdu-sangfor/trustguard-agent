---
name: ysoserial
description: Java deserialization payload generator for common gadget chains (CommonsBeanutils, CC1-CC6). Used as a helper skill to produce serialized Java payloads for delivery via other exploit skills.
metadata: { "runtime": { "emoji": "🔧", "requires": { "bins": ["java"], "env": [] }, "primaryEnv": null } }
---

# ysoserial

Generates serialized Java deserialization payloads using ysoserial for a range of common gadget chains.

## Target Framework

Any Java application with a vulnerable deserialization endpoint and one of: Commons Collections 1-6, Commons BeanUtils 1, Spring, Groovy, or other supported gadget chain libraries.

## Usage

Provide `params.chain` (gadget chain name) and `params.cmd` (command to embed). Returns a base64-encoded serialized payload ready for delivery.

## Parameters

| Param   | Type   | Required | Description                                      |
|---------|--------|----------|--------------------------------------------------|
| chain   | string | yes      | Gadget chain name (e.g. CommonsBeanutils1, CC6)  |
| cmd     | string | yes      | OS command to embed in the payload               |
| format  | string | no       | Output format: base64 (default) or raw           |
| timeout | int    | no       | Timeout in seconds (default: 30)                 |

## Notes

Commonly paired with shiro_exploit, exploit-weblogic, or any raw deserialization endpoint. The generated payload must be delivered over the appropriate protocol (HTTP body, cookie, T3, etc.).
