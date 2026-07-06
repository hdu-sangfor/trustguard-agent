from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Dict, Any
from io import StringIO
import subprocess
import tempfile


class SandboxOutputCapture:
    """Capture output from Python code execution"""
    
    def __init__(self):
        self.stdout = StringIO()
        self.stderr = StringIO()
    
    def get_output(self) -> str:
        return self.stdout.getvalue()
    
    def get_error(self) -> str:
        return self.stderr.getvalue()


def _create_execution_script(code: str, variables: Dict[str, Any]) -> str:
    """Create a temporary Python script for execution"""
    
    var_definitions = ""
    for key, value in variables.items():
        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
            try:
                if isinstance(value, str):
                    var_definitions += f'{key} = """{value}"""\n'
                else:
                    var_definitions += f'{key} = {json.dumps(value)}\n'
            except Exception:
                pass
    
    # Prepare user code with proper indentation
    # Handle both multi-line and single-line code properly
    if '\n' in code:
        # Multi-line code: indent each line
        indented_code = '\n    '.join(code.split('\n'))
    else:
        # Single-line code: no indentation needed
        indented_code = code
    
    script = f"""
import sys
import json
import re
import base64
import hashlib
import time
import random
import urllib.parse

try:
    import requests
except ImportError:
    pass

{var_definitions}

# Capture stdout
output = []
def custom_print(*args, **kwargs):
    output.append(' '.join(str(arg) for arg in args))

import builtins
builtins.print = custom_print

# Execute user code
try:
    {indented_code}
except Exception as e:
    print(f"Error: {{str(e)}}")

# Print collected output
sys.stdout.write('\\n'.join(output))
"""
    return script


def _execute_safely(code: str, timeout: int, variables: Dict[str, Any]) -> Dict[str, Any]:
    """Execute Python code safely with timeout"""
    
    start_time = time.perf_counter()
    
    try:
        script_content = _create_execution_script(code, variables)
        
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            temp_file = f.name
            f.write(script_content)
        
        try:
            # Execute with resource limits
            proc = subprocess.run(
                [sys.executable, temp_file],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tempfile.gettempdir()
            )
            
            execution_time = time.perf_counter() - start_time
            
            return {
                "status": "SUCCESS" if proc.returncode == 0 else "FAILED",
                "output": proc.stdout.strip(),
                "error": proc.stderr.strip() if proc.stderr else None,
                "execution_time_ms": int(execution_time * 1000),
                "returncode": proc.returncode
            }
            
        except subprocess.TimeoutExpired:
            return {
                "status": "TIMEOUT",
                "output": "",
                "error": f"Execution timeout after {timeout}s",
                "execution_time_ms": int((time.perf_counter() - start_time) * 1000),
                "returncode": -1
            }
            
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file)
            except Exception:
                pass
                
    except Exception as e:
        return {
            "status": "FAILED",
            "output": "",
            "error": str(e),
            "execution_time_ms": int((time.perf_counter() - start_time) * 1000),
            "returncode": -1
        }


def _validate_code(code: str) -> tuple[bool, str]:
    """Validate code for security"""
    
    dangerous_patterns = [
        'import os',
        'import subprocess',
        'import sys',
        '__import__',
        'eval(',
        'exec(',
        'compile(',
        'open(',
        'file(',
        'input(',
        'raw_input(',
        'globals()',
        'locals()',
        'vars()',
        'dir()',
        'getattr(',
        'setattr(',
        'delattr(',
        'hasattr(',
        '__dict__',
        '__class__',
        '__bases__',
        '__mro__',
        '__subclasses__',
    ]
    
    code_lower = code.lower()
    
    for pattern in dangerous_patterns:
        if pattern.lower() in code_lower:
            return False, f"Potentially dangerous pattern detected: {pattern}"
    
    return True, "Code is safe"


def _read_input() -> Dict[str, Any]:
    """Read input from arguments or stdin"""
    payload = {}
    
    # Check if we have a JSON payload as first argument
    if len(sys.argv) > 1 and sys.argv[1].strip():
        try:
            payload = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            pass
    
    # If no payload from args, try stdin
    if not payload and not sys.stdin.isatty():
        try:
            stdin_content = sys.stdin.read()
            if stdin_content.strip():
                payload = json.loads(stdin_content)
        except json.JSONDecodeError:
            pass
    
    return payload


def main() -> int:
    start_time = time.perf_counter()
    
    payload = _read_input()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    
    # Support both 'code' and 'script' parameter names
    code = str(params.get("code") or params.get("script") or "").strip()
    if not code:
        code = str(payload.get("code") or payload.get("script") or "").strip()
    
    timeout = int(params.get("timeout") or payload.get("timeout") or 30)
    variables = params.get("variables") if isinstance(params.get("variables"), dict) else {}
    mode = str(params.get("mode") or payload.get("mode") or "execute").strip().lower()
    
    if not code:
        out = {
            "status": "FAILED",
            "parsed_artifacts": {"error": "code/script parameter is required"},
            "raw_stdout": "",
            "raw_stderr": "missing code/script parameter",
            "duration_ms": int((time.perf_counter() - start_time) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 1
    
    # Validate code
    if mode == "validate":
        is_safe, message = _validate_code(code)
        out = {
            "status": "SUCCESS",
            "parsed_artifacts": {
                "is_safe": is_safe,
                "validation_message": message
            },
            "raw_stdout": f"Safe: {is_safe}\nMessage: {message}",
            "raw_stderr": "",
            "duration_ms": int((time.perf_counter() - start_time) * 1000),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    
    # Execute code
    execution_result = _execute_safely(code, timeout, variables)
    
    # Format output to match framework expectations
    parsed = {
        "execution_status": execution_result.get("status"),
        "output": execution_result.get("output", ""),
        "execution_time_ms": execution_result.get("execution_time_ms"),
    }
    
    if execution_result.get("error"):
        parsed["error"] = execution_result["error"]
    
    out = {
        "status": "SUCCESS" if execution_result.get("status") == "SUCCESS" else "FAILED",
        "parsed_artifacts": parsed,
        "raw_stdout": execution_result.get("output", ""),
        "raw_stderr": execution_result.get("error", ""),
        "duration_ms": int((time.perf_counter() - start_time) * 1000),
    }
    
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())