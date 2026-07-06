#!/usr/bin/env python3
"""
Enhanced Metasploit with Session Management and Module Search
支持 session 交互、持久连接和模块搜索的完整 Metasploit 替代技能
"""

import json
import subprocess
import time
import sys
import shlex
import re
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
import threading
import queue
import signal
import select
import yaml

try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False

class MetasploitSessionManager:
    """Metasploit Session 管理器"""
    
    def __init__(self):
        self.sessions = {}
        self.session_counter = 0
        self.temp_files = []
        
    def create_session(self, session_info: Dict) -> str:
        """创建新的 session"""
        session_id = str(self.session_counter)
        self.session_counter += 1
        
        session = {
            "id": session_id,
            "target": session_info.get("target", ""),
            "module": session_info.get("module", ""),
            "status": "active",
            "created_at": time.time(),
            "last_activity": time.time(),
            "command_history": [],
            "interactive_mode": False,
            "process": None,
            "thread": None
        }
        
        self.sessions[session_id] = session
        return session_id
    
    def execute_command(self, session_id: str, command: str) -> Dict:
        """在指定 session 中执行命令"""
        if session_id not in self.sessions:
            return {"success": False, "error": "Session not found"}
        
        session = self.sessions[session_id]
        
        # 构建完整的 msfconsole 命令
        msf_command = f"use {session['module']}"
        
        if command.startswith("session"):
            msf_command = f"session {command}"
        else:
            msf_command += f"\n{command}"
        
        # 更新会话活动时间
        session["last_activity"] = time.time()
        session["command_history"].append({
            "command": command,
            "timestamp": time.time()
        })
        
        # 在新的线程中执行命令
        result_queue = queue.Queue()
        
        def execute_command_thread():
                    try:
                        if not PEXPECT_AVAILABLE:
                            # 使用 subprocess 作为备选方案
                            result = subprocess.run(
                                ["msfconsole", "-x", msf_command],
                                capture_output=True,
                                text=True,
                                timeout=60
                            )
                            output = result.stdout + result.stderr
                        else:
                            # 使用 pexpect 进行交互式执行
                            child = pexpect.spawn(f"msfconsole", encoding='utf-8', timeout=30)
                            
                            # 设置信号处理
                            def handle_signal(signum, frame):
                                child.close(force=True)
                                result_queue.put({"success": False, "error": "Command interrupted"})
                            
                            signal.signal(signal.SIGINT, handle_signal)
                            signal.signal(signal.SIGTERM, handle_signal)
                            
                            # 发送命令
                            child.sendline(msf_command)
                            
                            # 等待命令完成
                            output = ""
                            while True:
                                try:
                                    line = child.readline()
                                    if line == "":
                                        break
                                    output += line
                                    
                                    # 检查是否完成
                                    if re.search(r'msfconsole.*>', line) or re.search(r'session.*>', line):
                                        break
                                        
                                except Exception as e:
                                    output += f"\nError: {e}\n"
                                    break
                            
                            child.close()
                        
                        result_queue.put({
                            "success": True,
                            "output": output,
                            "session_id": session_id
                        })
                        
                    except Exception as e:
                        result_queue.put({
                            "success": False,
                            "error": str(e)
                        })
        
        # 启动执行线程
        session["thread"] = threading.Thread(target=execute_command_thread)
        session["thread"].start()
        
        # 等待结果
        try:
            result = result_queue.get(timeout=60)
            return result
        except queue.Empty:
            return {"success": False, "error": "Command timeout"}
    
    def get_session_info(self, session_id: str) -> Dict:
        """获取 session 信息"""
        if session_id not in self.sessions:
            return {"success": False, "error": "Session not found"}
        
        session = self.sessions[session_id]
        return {
            "success": True,
            "session": {
                "id": session["id"],
                "target": session["target"],
                "module": session["module"],
                "status": session["status"],
                "created_at": session["created_at"],
                "last_activity": session["last_activity"],
                "command_history": session["command_history"][-5:],  # 只返回最近5条命令
                "interactive_mode": session["interactive_mode"]
            }
        }
    
    def list_sessions(self) -> Dict:
        """列出所有活动 sessions"""
        active_sessions = []
        
        for session_id, session in self.sessions.items():
            if session["status"] == "active":
                active_sessions.append({
                    "id": session["id"],
                    "target": session["target"],
                    "module": session["module"],
                    "created_at": session["created_at"],
                    "last_activity": session["last_activity"]
                })
        
        return {
            "success": True,
            "sessions": active_sessions,
            "total": len(active_sessions)
        }
    
    def close_session(self, session_id: str) -> Dict:
        """关闭指定 session"""
        if session_id not in self.sessions:
            return {"success": False, "error": "Session not found"}
        
        session = self.sessions[session_id]
        
        # 清理资源
        if session.get("process"):
            try:
                session["process"].terminate()
            except:
                pass
        
        # 关闭线程
        if session.get("thread"):
            session["thread"].join(timeout=5)
        
        # 更新状态
        session["status"] = "closed"
        
        return {"success": True, "message": f"Session {session_id} closed"}
    
    def cleanup_expired_sessions(self, max_idle_time: int = 3600) -> Dict:
        """清理过期的 sessions"""
        current_time = time.time()
        cleaned_count = 0
        
        for session_id, session in list(self.sessions.items()):
            idle_time = current_time - session["last_activity"]
            if idle_time > max_idle_time:
                self.close_session(session_id)
                cleaned_count += 1
                del self.sessions[session_id]
        
        return {"success": True, "cleaned_sessions": cleaned_count}

class EnhancedMetasploitSkill:
    """增强的 Metasploit 技能"""
    
    def __init__(self):
        self.session_manager = MetasploitSessionManager()
        self.current_session_id = None
        
    def search_modules(self, search_query: str) -> Dict:
        """搜索 Metasploit 模块"""
        try:
            # 构建搜索脚本
            search_script = f"search {search_query};exit"
            
            # 执行搜索
            result = subprocess.run(
                ["msfconsole", "-q", "-x", search_script],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            # 解析搜索结果
            search_results = []
            lines = result.stdout.splitlines()
            
            for line in lines:
                line = line.strip()
                if line and not line.startswith("msfconsole") and not line.startswith("search"):
                    # 模块名称行
                    if "exploit/" in line or "auxiliary/" in line:
                        module_info = {
                            "name": line,
                            "type": "exploit" if "exploit/" in line else "auxiliary"
                        }
                        search_results.append(module_info)
            
            return {
                "success": True,
                "search_query": search_query,
                "results": search_results,
                "count": len(search_results),
                "raw_output": result.stdout
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "search_query": search_query,
                "results": []
            }
        
    def _build_msf_command(self, target: str, params: Dict) -> List[str]:
        """构建 msfconsole 命令"""
        # 获取工具信息
        try:
            info = self._load_tool_info("metasploit-session")
        except:
            info = {"name": "metasploit-session"}
        
        # 构建基础命令
        cmd = ["msfconsole", "-q"]
        
        # 添加参数
        if params.get("extra"):
            cmd.extend(params["extra"])
        
        return cmd
    
    def _parse_output(self, output: str) -> Dict:
        """解析 msfconsole 输出"""
        result = {
            "sessions": [],
            "vulnerabilities": [],
            "modules": [],
            "info": []
        }
        
        lines = output.splitlines()
        for line in lines:
            # 检测 session 创建
            session_match = re.search(r"Session (\d+) opened", line)
            if session_match:
                session_id = session_match.group(1)
                result["sessions"].append({
                    "id": session_id,
                    "line": line.strip()
                })
            
            # 检测模块信息（搜索结果）
            if "exploit/" in line or "auxiliary/" in line or "/post" in line:
                module_name = line.strip()
                if module_name and not module_name.startswith("msfconsole"):
                    result["modules"].append(module_name)
            
            # 检测漏洞信息
            if "vulnerability" in line.lower() or "exploit" in line.lower():
                result["vulnerabilities"].append(line.strip())
            
            # 收集其他信息
            if line.strip() and not line.startswith("msfconsole"):
                result["info"].append(line.strip())
        
        return result
    
    def _create_temp_file(self, content: str) -> str:
        """创建临时文件"""
        temp_file = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
        temp_file.write(content)
        temp_file.close()
        self.session_manager.temp_files.append(temp_file.name)
        return temp_file.name
    
    def exploit_and_session(self, target: str, params: Dict) -> Dict:
        """执行漏洞利用并创建 session"""
        try:
            # 创建 msfconsole 脚本
            script_content = self._generate_exploit_script(target, params)
            script_file = self._create_temp_file(script_content)
            
            # 构建命令
            cmd = ["msfconsole", "-q", "-r", script_file]
            
            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            # 清理临时文件
            if os.path.exists(script_file):
                os.unlink(script_file)
            
            # 解析输出
            parsed = self._parse_output(result.stdout + result.stderr)
            
            # 如果检测到 session，创建会话管理
            if parsed.get("sessions"):
                session_info = {
                    "target": target,
                    "module": params.get("modules", "exploit/multi/http/struts2_content_type_ognl")
                }
                session_id = self.session_manager.create_session(session_info)
                parsed["session_id"] = session_id
                self.current_session_id = session_id
            
            return {
                "success": result.returncode == 0,
                "parsed_artifacts": parsed,
                "raw_stdout": result.stdout,
                "raw_stderr": result.stderr,
                "returncode": result.returncode
            }
            
        except subprocess.TimeoutExpired as e:
            return {
                "success": False,
                "error": "Command timeout",
                "parsed_artifacts": {"error": "timeout after 300s"},
                "raw_stdout": "",
                "raw_stderr": "timeout after 300s"
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "parsed_artifacts": {"error": str(e)}
            }
    
    def _generate_exploit_script(self, target: str, params: Dict) -> str:
        """生成漏洞利用脚本"""
        module = params.get("modules", "exploit/multi/http/struts2_content_type_ognl")
        script_lines = []
        
        # 处理模块名称
        if module.startswith("search"):
            script_lines.append(module)
            script_lines.append("exit")
            return "\n".join(script_lines)
        else:
            script_lines.append(f"use {module}")
        
        # 添加目标设置
        if target:
            if target.startswith(("http://", "https://")):
                # HTTP URL 目标
                from urllib.parse import urlparse
                parsed_url = urlparse(target)
                host = parsed_url.hostname or parsed_url.netloc.split(':')[0]
                port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
                path = parsed_url.path
                
                script_lines.append(f"set RHOSTS {host}")
                script_lines.append(f"set RPORT {port}")
                
                if path and path != "/":
                    script_lines.append(f"set TARGETURI {path}")
            else:
                # IP 地址或主机名
                script_lines.append(f"set RHOSTS {target}")
                if params.get("RPORT"):
                    script_lines.append(f"set RPORT {params['RPORT']}")
        
        # 合并参数
        merged_options = {}
        
        # 从 params.options 中获取参数
        if isinstance(params.get("options"), dict):
            merged_options.update(params["options"])
        
        # 从 params 中直接获取参数（兼容性）
        option_mappings = {
            "lhost": "LHOST",
            "lport": "LPORT",
            "payload": "PAYLOAD",
            "cmd": "CMD",
            "targeturi": "TARGETURI",
            "command": "command",
            "rport": "RPORT"
        }
        
        for lower_key, upper_key in option_mappings.items():
            if lower_key in params and upper_key not in merged_options:
                merged_options[upper_key] = params[lower_key]
            elif upper_key in params and upper_key not in merged_options:
                merged_options[upper_key] = params[upper_key]
        
        # 添加参数到脚本
        use_check = False
        for key, value in merged_options.items():
            if value is not None and str(value).strip():
                if key.lower() == "command" and str(value).lower() == "check":
                    use_check = True
                elif key.upper() not in ["RHOSTS", "RPORT", "COMMAND"]:
                    # 允许 TARGETURI 被设置，以覆盖从 URL 解析的值
                    script_lines.append(f"set {key.upper()} {value}")
        
        # 添加执行命令
        if use_check:
            script_lines.append("check")
        else:
            script_lines.append("exploit")
        
        script_lines.append("exit")
        
        return "\n".join(script_lines)
    
    def session_interactive(self, session_id: str, command: str) -> Dict:
        """在 session 中执行交互式命令"""
        return self.session_manager.execute_command(session_id, command)
    
    def session_list(self) -> Dict:
        """列出所有 sessions"""
        return self.session_manager.list_sessions()
    
    def session_info(self, session_id: str) -> Dict:
        """获取 session 信息"""
        return self.session_manager.get_session_info(session_id)
    
    def session_close(self, session_id: str) -> Dict:
        """关闭 session"""
        return self.session_manager.close_session(session_id)

def _load_tool_info(tool_name: str) -> Dict:
    """加载工具信息"""
    return {"name": tool_name}

def main() -> int:
    """主函数"""
    start = time.perf_counter()
    
    # 读取输入
    payload = {}
    if len(sys.argv) > 1 and sys.argv[1].strip():
        try:
            payload = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            input_file = sys.argv[1].strip()
            if os.path.exists(input_file):
                payload = json.loads(Path(input_file).read_text(encoding="utf-8"))
    elif not sys.stdin.isatty():
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError:
            pass
    
    # 创建技能实例
    skill = EnhancedMetasploitSkill()
    
    # 解析参数
    target = str(payload.get("target") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    mode = params.get("mode", "exploit")
    
    timeout = int(params.get("timeout") or 300)
    hard_cap = int(os.getenv("METASPLOIT_HARD_TIMEOUT_SECONDS", "300"))
    if timeout <= 0:
        timeout = 300
    timeout = min(timeout, hard_cap)
    
    try:
        if mode == "search":
            # 模块搜索模式
            search_query = params.get("modules") or params.get("search_query", "")
            if not search_query:
                error_output = {
                    "status": "FAILED",
                    "error": "search_query is required for search mode",
                    "execution_time": time.perf_counter() - start
                }
                print(json.dumps(error_output, indent=2, ensure_ascii=False))
                return 1
            
            result = skill.search_modules(search_query)
            
        elif mode == "session_interactive":
            # Session 交互模式
            session_id = params.get("session_id")
            command = params.get("command", "")
            
            if not session_id:
                error_output = {
                    "status": "FAILED",
                    "error": "session_id is required for session_interactive mode",
                    "execution_time": time.perf_counter() - start
                }
                print(json.dumps(error_output, indent=2, ensure_ascii=False))
                return 1
            
            if not command:
                error_output = {
                    "status": "FAILED", 
                    "error": "command is required for session_interactive mode",
                    "execution_time": time.perf_counter() - start
                }
                print(json.dumps(error_output, indent=2, ensure_ascii=False))
                return 1
            
            result = skill.session_interactive(session_id, command)
            
        elif mode == "session_list":
            # 列出 sessions
            result = skill.session_list()
            
        elif mode == "session_info":
            # 获取 session 信息
            session_id = params.get("session_id")
            if not session_id:
                error_output = {
                    "status": "FAILED",
                    "error": "session_id is required for session_info mode",
                    "execution_time": time.perf_counter() - start
                }
                print(json.dumps(error_output, indent=2, ensure_ascii=False))
                return 1
            
            result = skill.session_info(session_id)
            
        elif mode == "session_close":
            # 关闭 session
            session_id = params.get("session_id")
            if not session_id:
                error_output = {
                    "status": "FAILED",
                    "error": "session_id is required for session_close mode",
                    "execution_time": time.perf_counter() - start
                }
                print(json.dumps(error_output, indent=2, ensure_ascii=False))
                return 1
            
            result = skill.session_close(session_id)
            
        elif mode == "exploit":
            # 漏洞利用模式
            result = skill.exploit_and_session(target, params)
            
        else:
            error_output = {
                "status": "FAILED",
                "error": f"Unknown mode: {mode}",
                "execution_time": time.perf_counter() - start
            }
            print(json.dumps(error_output, indent=2, ensure_ascii=False))
            return 1
        
        # 构建输出
        output = {
            "status": "SUCCESS" if result.get("success", False) else "FAILED",
            "data": result,
            "execution_time": time.perf_counter() - start
        }
        
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
        
    except Exception as e:
        error_output = {
            "status": "FAILED",
            "error": str(e),
            "execution_time": time.perf_counter() - start
        }
        print(json.dumps(error_output, indent=2, ensure_ascii=False))
        return 1

if __name__ == "__main__":
    main()