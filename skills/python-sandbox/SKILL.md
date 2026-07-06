---
name: python-sandbox
description: Secure Python execution sandbox for custom payload testing and HTTP requests. Allows agents to execute Python code with common libraries for penetration testing.
metadata: { "runtime": { "emoji": "🐍", "requires": { "bins": [], "env": [] }, "primaryEnv": null } }
---

# Python Sandbox

Secure Python execution environment for testing custom payloads and HTTP requests during penetration testing.

## Usage

Execute Python code in a sandboxed environment with access to common penetration testing libraries.

## Parameters

| Param   | Type   | Required | Description                    |
|---------|--------|----------|--------------------------------|
| code    | string | yes      | Python code to execute         |
| timeout | int    | no       | Execution timeout in seconds (default 30) |
| variables | dict | no       | Pre-defined variables for code execution |
| mode    | string | no       | Execution mode: "execute" (default) or "validate" |

## Available Libraries

- `requests` - HTTP client for custom requests
- `json` - JSON parsing and generation
- `re` - Regular expressions
- `base64` - Base64 encoding/decoding
- `urllib.parse` - URL parsing and encoding
- `hashlib` - Hash functions
- `time` - Time utilities
- `random` - Random generation

## Common Use Cases

### Custom HTTP Payload Testing
```json
{
  "code": "import requests; requests.post('http://target.com/api', json={'test': 'payload'}).headers",
  "timeout": 10
}
```

### Custom Payload Generation
```json
{
  "code": "import base64; print(base64.b64encode(b'cmd.exe /c whoami').decode())",
  "timeout": 5
}
```

### Response Analysis
```json
{
  "code": "import json, re; response = requests.get('http://target.com'); print(re.findall(r'flag{.*?}', response.text))",
  "timeout": 15
}
```

## Security Features

- Execution timeout limit
- Memory and CPU resource limits
- Network access allowed for HTTP requests
- No direct filesystem access
- No subprocess execution
- No network socket creation beyond HTTP

## Output Format

Returns execution results including:
- `status`: SUCCESS/FAILED/TIMEOUT
- `output`: Code execution output
- `error`: Error messages if any
- `execution_time_ms`: Execution time in milliseconds
