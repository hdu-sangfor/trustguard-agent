name: webshell-php
description: PHP Webshell for remote command execution on vulnerable web servers. Supports file upload, command execution, and file management through web requests.
metadata: { "runtime": { "emoji": "🕷️", "requires": { "bins": ["curl", "python"], "env": [] }, "primaryEnv": null } }
---

# PHP Webshell

PHP Webshell for remote command execution on vulnerable web servers. Allows direct command execution without needing reverse shell setup.

## Usage

Used by the executor with `target` (web server URL) and optional `params.options`, `params.timeout`.

## Parameters

| Param   | Type   | Required | Description |
|---------|--------|----------|-------------|
| target  | string | yes      | Web server URL (e.g., http://target.com/shell.php) |
| command | string | no       | Command to execute (default: empty for interactive mode) |
| file    | string | no       | File to upload/download (e.g., /path/to/local/file) |
| action  | string | no       | Action type: upload, download, exec, list (default: exec) |
| timeout | int    | no       | Timeout in seconds (default: 30) |

## Examples

### Execute Command
Execute command on target web server:
```json
{
  "target": "http://target.com/uploads/shell.php",
  "options": {
    "command": "whoami",
    "action": "exec"
  },
  "timeout": 30
}
```

### List Directory
List directory contents on target server:
```json
{
  "target": "http://target.com/uploads/shell.php",
  "options": {
    "command": "ls -la /var/www/html/",
    "action": "exec"
  },
  "timeout": 30
}
```

### Upload File
Upload file to target server:
```json
{
  "target": "http://target.com/uploads/shell.php",
  "options": {
    "file": "/path/to/local/file.txt",
    "action": "upload"
  },
  "timeout": 60
}
```

### Download File
Download file from target server:
```json
{
  "target": "http://target.com/uploads/shell.php",
  "options": {
    "file": "/etc/passwd",
    "action": "download"
  },
  "timeout": 30
}
```

### Interactive Mode
Interactive command execution mode:
```json
{
  "target": "http://target.com/uploads/shell.php",
  "timeout": 300
}
```

## Features

- **Command Execution**: Execute shell commands directly on target server
- **File Operations**: Upload/download files from target server
- **No Reverse Shell**: No need to setup listener or VPS
- **HTTP Based**: Uses standard HTTP requests
- **Multiple Actions**: Upload, download, execute, and list operations

## Security Notes

- Only use on systems you have permission to test
- Webshell execution is detectable by security systems
- Use responsibly and in authorized environments
- Remove webshell after testing completion