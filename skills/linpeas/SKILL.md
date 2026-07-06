name: linpeas
description: Linux Privilege Escalation Awesome Script (linPEAS). Automated script to search for privilege escalation paths on Linux systems.
metadata: { "runtime": { "emoji": "⚡", "requires": { "bins": ["curl", "wget"], "env": [] }, "primaryEnv": null } }
---

# LinPEAS - Linux Privilege Escalation

LinPEAS is a script that searches for possible privilege escalation paths that you can exploit on Linux systems. It's an automated tool that performs many checks for privilege escalation opportunities.

## Usage

Used by the executor with `target` (webshell URL or local execution) and optional `params.options`, `params.timeout`.

## Parameters

| Param     | Type   | Required | Description |
|-----------|--------|----------|-------------|
| target    | string | no       | Webshell URL (optional for local execution) |
| mode      | string | no       | Mode: download, execute, full (default: full) |
| profile   | string | no       | Profile: quick, stealth, full (default: full) |
| timeout   | int    | no       | Timeout in seconds (default: 300) |

## Examples

### Full Privilege Escalation Scan
Run full privilege escalation check:
```json
{
  "target": "http://target.com/shell.php",
  "options": {
    "mode": "execute",
    "profile": "full"
  },
  "timeout": 300
}
```

### Quick Scan
Quick privilege escalation check:
```json
{
  "target": "http://target.com/shell.php",
  "options": {
    "mode": "execute",
    "profile": "quick"
  },
  "timeout": 120
}
```

### Stealth Mode
Stealth mode for low profile checks:
```json
{
  "target": "http://target.com/shell.php",
  "options": {
    "mode": "execute",
    "profile": "stealth"
  },
  "timeout": 180
}
```

### Download Script Only
Download linPEAS script to target:
```json
{
  "target": "http://target.com/shell.php",
  "options": {
    "mode": "download"
  },
  "timeout": 60
}
```

### Local Execution
Run locally on Linux system:
```json
{
  "options": {
    "profile": "full"
  },
  "timeout": 300
}
```

## Features

- **Comprehensive Checks**: Covers most common privilege escalation vectors
- **Multiple Profiles**: Quick, stealth, and full scanning modes
- **Automated Detection**: Automatically identifies vulnerabilities
- **Detailed Reporting**: Provides clear findings and recommendations
- **Webshell Compatible**: Can be executed through webshell

## Check Categories

- **System Information**: OS version, kernel, users, groups
- **Network Information**: Network interfaces, open ports
- **File Permissions**: SUID, SGID, world-writable files
- **Environment Variables**: PATH, LD_LIBRARY_PATH, etc.
- **Services**: Running services, cron jobs
- **Configuration Files**: Interesting config files
- **Credentials**: Search for passwords and keys

## Profile Differences

- **Quick**: Fast checks focusing on high-impact vulnerabilities
- **Stealth**: Low-profile checks to avoid detection
- **Full**: Comprehensive check of all privilege escalation vectors

## Security Notes

- Only run on systems you have permission to test
- May generate suspicious network traffic
- Output may contain sensitive information
- Clean up script after testing completion