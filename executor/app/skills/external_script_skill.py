from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.models import SkillRequest, SkillResult
from app.skills.base import Skill

logger = logging.getLogger(__name__)


class ExternalScriptSkill(Skill):
    def __init__(self, skill_id: str, category: str, script_path: Path):
        self.id = skill_id
        self.category = category
        self._script_path = script_path

    @staticmethod
    def _to_text(data: Any) -> str:
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    @staticmethod
    def _parse_json_output(stdout: str) -> dict[str, Any] | None:
        content = (stdout or "").strip()
        if not content:
            return None
        try:
            data = json.loads(content)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        for line in reversed(content.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                return data if isinstance(data, dict) else None
            except Exception:
                continue
        return None

    @staticmethod
    def _effective_timeout(skill_id: str | None, req_timeout: int | None) -> int:
        """
        包裹整段 docker run / 子进程的上层超时。
        未传 params.timeout 时用 default；nuclei 等重扫描默认远高于 90s，否则脚本内 300s 子进程从未执行完就会被外层杀死。
        """
        sid = (skill_id or "").strip().lower()
        default_timeout = int(os.getenv("EXECUTOR_SCRIPT_DEFAULT_TIMEOUT_SECONDS", "90"))
        hard_cap = int(os.getenv("EXECUTOR_SCRIPT_HARD_TIMEOUT_SECONDS", "120"))

        if sid == "katana":
            default_timeout = int(
                os.getenv("EXECUTOR_SCRIPT_KATANA_DEFAULT_TIMEOUT_SECONDS", "600")
            )
            hard_cap = int(os.getenv("EXECUTOR_SCRIPT_KATANA_HARD_TIMEOUT_SECONDS", "900"))

        elif sid == "dispatcher":
            default_timeout = int(
                os.getenv("EXECUTOR_SCRIPT_DISPATCHER_DEFAULT_TIMEOUT_SECONDS", "300")
            )
            hard_cap = int(os.getenv("EXECUTOR_SCRIPT_DISPATCHER_HARD_TIMEOUT_SECONDS", "600"))

        elif sid == "nuclei":
            # 与 .env.example 及 skills/nuclei 内子进程预算一致，避免外层 docker run 先于 nuclei 子进程超时
            default_timeout = int(
                os.getenv("EXECUTOR_SCRIPT_NUCLEI_DEFAULT_TIMEOUT_SECONDS", "300")
            )
            hard_cap = int(os.getenv("EXECUTOR_SCRIPT_NUCLEI_HARD_TIMEOUT_SECONDS", "600"))

        if req_timeout is None or req_timeout == "":
            requested = default_timeout
        else:
            try:
                requested = int(req_timeout)
            except (TypeError, ValueError):
                requested = default_timeout
        if requested <= 0:
            requested = default_timeout
        core = min(requested, hard_cap)
        return core

    def _skill_image(self) -> str:
        image_prefix = (os.getenv("EXECUTOR_SKILL_IMAGE_PREFIX") or "trustguard-skill-").strip()
        return f"{image_prefix}{self.id}"

    def _use_skill_container(self) -> bool:
        return os.getenv("EXECUTOR_USE_SKILL_CONTAINERS", "true").lower() == "true"

    @staticmethod
    def _looks_like_missing_entrypoint(stderr: str, payload: dict[str, Any]) -> bool:
        text = (stderr or "").lower()
        if not text:
            return False
        if "oci runtime create failed" not in text:
            return False
        if "no such file or directory" not in text:
            return False
        payload_probe = f"\"skill_id\": \"{str(payload.get('skill_id') or '').strip()}\"".lower()
        return payload_probe in text or "\"task_id\":" in text

    @staticmethod
    def _looks_like_missing_runpy(stderr: str) -> bool:
        text = (stderr or "").lower()
        if not text:
            return False
        return (
            "can't open file" in text
            and "run.py" in text
            and "no such file or directory" in text
        )

    @staticmethod
    def _ensure_dir(path_text: str) -> str:
        p = Path(path_text).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def _run_in_container(
        self,
        payload: dict[str, Any],
        timeout: int,
    ) -> subprocess.CompletedProcess[Any]:
        workspace_root = os.getenv("WORKSPACE_ROOT", "/data/workspace")
        # docker volume mount：Windows host path 形如 `D:\xxx` 含 `:`，若容器路径也用同样格式会导致 docker 解析成 invalid mode。
        # 因此：仅用宿主 `WORKSPACE_ROOT` 做 host mount；容器内统一挂到 `/data/workspace`。
        host_workspace_root = str(workspace_root).strip().rstrip("\\/").rstrip(".")
        container_workspace_root = os.getenv("SKILL_CONTAINER_WORKSPACE_ROOT", "/data/workspace").strip()
        use_volumes_from = False
        volumes_from_target = ""
        if str(workspace_root).startswith("/"):
            # executor/service 若本身就在 Linux 容器内，WORKSPACE_ROOT 通常已是 `/data/workspace`。
            # 这种情况下也保持容器侧与 WORKSPACE_ROOT 一致。
            container_workspace_root = str(workspace_root).strip()
            # 关键：executor 运行在容器内且通过宿主 docker.sock 启 skill 容器时，
            # 不能再用 `-v /data/workspace:/data/workspace`（宿主并无该路径）。
            # 改为继承 executor 容器挂载，确保 workspace 与 artifacts 一致。
            volumes_from_target = (os.getenv("HOSTNAME") or "").strip()
            use_volumes_from = bool(volumes_from_target)
        env_opts: list[str] = []
        for key in (
            "BAIDU_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            val = os.getenv(key)
            if val:
                env_opts.extend(["-e", f"{key}={val}"])
        volume_opts: list[str] = []
        if not use_volumes_from:
            volume_opts.append(f"{host_workspace_root}:{container_workspace_root}")

        script_name = (self._script_path.name if self._script_path else "").strip().lower()
        use_run_py = script_name == "run.py"
        tools_root = os.getenv("TRUSTGUARD_TOOLS_ROOT", "")
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            os.getenv("EXECUTOR_DOCKER_NETWORK", "trustguard-agent_default"),
            "--add-host",
            "host.docker.internal:host-gateway",
            *env_opts,
            "-e",
            f"WORKSPACE_ROOT={container_workspace_root}",
            "-e",
            f"TOOLS_REGISTRY_YAML={os.getenv('TOOLS_REGISTRY_YAML', '')}",
            "-e",
            f"TRUSTGUARD_TOOLS_ROOT={tools_root}",
        ]
        if use_volumes_from:
            docker_cmd.extend(["--volumes-from", volumes_from_target])
        for vol in volume_opts:
            docker_cmd.extend(["-v", vol])
        if use_run_py:
            docker_cmd.extend(["--entrypoint", "python3", self._skill_image(), "run.py"])
            docker_cmd.append(json.dumps(payload, ensure_ascii=False))
        else:
            docker_cmd.append(self._skill_image())
            docker_cmd.append(json.dumps(payload, ensure_ascii=False))
        proc = subprocess.run(docker_cmd, capture_output=True, text=False, timeout=timeout)
        stderr_text = self._to_text(proc.stderr)
        missing_entrypoint = self._looks_like_missing_entrypoint(stderr_text, payload)
        missing_runpy = self._looks_like_missing_runpy(stderr_text)
        if proc.returncode == 0 or not (missing_entrypoint or missing_runpy):
            return proc
        fallback_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            os.getenv("EXECUTOR_DOCKER_NETWORK", "trustguard-agent_default"),
            "--add-host",
            "host.docker.internal:host-gateway",
            *env_opts,
            "-e",
            f"WORKSPACE_ROOT={container_workspace_root}",
            "-e",
            f"TOOLS_REGISTRY_YAML={os.getenv('TOOLS_REGISTRY_YAML', '')}",
            "-e",
            f"TRUSTGUARD_TOOLS_ROOT={tools_root}",
            "--entrypoint",
            "python3",
        ]
        if use_volumes_from:
            fallback_cmd.extend(["--volumes-from", volumes_from_target])
        for vol in volume_opts:
            fallback_cmd.extend(["-v", vol])
        fallback_cmd += [
            self._skill_image(),
            "scripts/execute.py",
        ]
        fallback_cmd.append(json.dumps(payload, ensure_ascii=False))
        return subprocess.run(fallback_cmd, capture_output=True, text=False, timeout=timeout)

    def _attach_kb_features_if_declared(self, req: SkillRequest, artifacts: dict[str, Any] | None) -> None:
        """KB-R1c：注册表声明且存在 kb_features.py 时，写入 artifacts['kb_features']。"""
        if not isinstance(artifacts, dict):
            return
        try:
            from app.kb_feature_policy import extract_kb_features_declared
        except Exception:
            return
        if not extract_kb_features_declared(self.id):
            return
        script_path = self._script_path.resolve()
        # 兼容两种入口：skills/<id>/scripts/execute.py 与 skills/<id>/run.py
        skill_root = script_path.parent.parent if script_path.parent.name == "scripts" else script_path.parent
        mod_path = skill_root / "kb_features.py"
        if not mod_path.is_file():
            logger.warning("extract_kb_features declared for %s but missing %s", self.id, mod_path)
            return
        try:
            import importlib.util

            mod_name = f"_skill_kb_{self.id.replace('-', '_')}"
            spec = importlib.util.spec_from_file_location(mod_name, str(mod_path))
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "extract_kb_features", None)
            if not callable(fn):
                return
            feats = fn(artifacts, req.context or {})
            if isinstance(feats, dict) and feats:
                artifacts["kb_features"] = feats
        except Exception:
            logger.debug("extract_kb_features failed skill_id=%s", self.id, exc_info=True)

    def execute(self, req: SkillRequest) -> SkillResult:
        req_ctx = dict(req.context or {})
        payload = {
            "task_id": req.task_id,
            "skill_id": req.skill_id,
            "target": req.target,
            "params": req.params or {},
            "context": req_ctx,
            "allowed_target": req.allowed_target,
        }
        start = time.perf_counter()
        timeout = self._effective_timeout(req.skill_id, (req.params or {}).get("timeout"))
        try:
            if self._use_skill_container():
                proc = self._run_in_container(payload, timeout)
            else:
                proc = subprocess.run(
                    [sys.executable, str(self._script_path), json.dumps(payload, ensure_ascii=False)],
                    cwd=str(self._script_path.parent),
                    capture_output=True,
                    text=False,
                    timeout=timeout,
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            stdout_text = self._to_text(proc.stdout)
            stderr_text = self._to_text(proc.stderr)
            parsed = self._parse_json_output(stdout_text)
            if parsed is not None:
                pa = parsed.get("parsed_artifacts")
                if isinstance(pa, dict):
                    self._attach_kb_features_if_declared(req, pa)
                status_from_payload = str(parsed.get("status") or "").strip()
                status_upper = status_from_payload.upper() if status_from_payload else ""
                # 外层进程非 0 时，禁止脚本内 JSON 把结果伪装成 SUCCESS，避免错误被吞噬。
                if proc.returncode != 0 and status_upper in ("OK", "SUCCESS"):
                    if isinstance(pa, dict):
                        pa.setdefault("returncode", proc.returncode)
                        pa.setdefault("status_masked_by_payload", True)
                    status_from_payload = "FAILED"
                return SkillResult(
                    status=status_from_payload or ("SUCCESS" if proc.returncode == 0 else "FAILED"),
                    parsed_artifacts=pa if isinstance(pa, dict) else parsed.get("parsed_artifacts"),
                    raw_stdout=str(parsed.get("raw_stdout") or stdout_text or ""),
                    raw_stderr=str(parsed.get("raw_stderr") or stderr_text or ""),
                    duration_ms=int(parsed.get("duration_ms") or duration_ms),
                )
            fallback_artifacts: dict[str, Any] = {
                "raw_preview": stdout_text[:2000],
                "stderr_preview": stderr_text[:500],
                "returncode": proc.returncode,
            }
            return SkillResult(
                status="SUCCESS" if proc.returncode == 0 else "FAILED",
                parsed_artifacts=fallback_artifacts,
                raw_stdout=stdout_text,
                raw_stderr=stderr_text,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as e:
            # Python 3.7+：TimeoutExpired 在 kill 子进程后可能仍带有部分 stdout（如 katana 已打印 JSON）
            partial_out = self._to_text(getattr(e, "stdout", None))
            partial_err = self._to_text(getattr(e, "stderr", None))
            parsed_timeout = self._parse_json_output(partial_out)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if parsed_timeout is not None:
                pa_t = parsed_timeout.get("parsed_artifacts")
                if isinstance(pa_t, dict):
                    self._attach_kb_features_if_declared(req, pa_t)
                return SkillResult(
                    status=str(parsed_timeout.get("status") or "SUCCESS"),
                    parsed_artifacts=pa_t if isinstance(pa_t, dict) else parsed_timeout.get("parsed_artifacts"),
                    raw_stdout=str(parsed_timeout.get("raw_stdout") or partial_out or ""),
                    raw_stderr=str(parsed_timeout.get("raw_stderr") or partial_err or ""),
                    duration_ms=int(parsed_timeout.get("duration_ms") or duration_ms),
                )
            return SkillResult(
                status="TIMEOUT",
                parsed_artifacts={
                    "error": "external skill script timeout",
                    "skill_id": req.skill_id,
                    "executor_timeout_seconds": timeout,
                    "partial_stdout_preview": (partial_out or "")[:4000],
                    "hint": (
                        "外层超时（docker run / 本地子进程）已到。秒数是「整容器/整脚本」预算，"
                        "Katana 等可能已在 WORKSPACE 内写出 discovery/katana_urls.txt，"
                        "执行器会尝试按落盘结果恢复；若仍失败可调 params.timeout 或 "
                        "EXECUTOR_SCRIPT_KATANA_* / EXECUTOR_SCRIPT_NUCLEI_*（nuclei 还可设 nuclei_timeout）。"
                    ),
                },
                raw_stdout=partial_out or "",
                raw_stderr=(partial_err or "") + f" | timeout after {timeout}s (executor wrap for skill_id={req.skill_id})",
                duration_ms=duration_ms,
            )
        except FileNotFoundError as exc:
            missing = str(exc)
            return SkillResult(
                status="FAILED",
                parsed_artifacts={"error": f"runtime executable missing: {missing}"},
                raw_stdout="",
                raw_stderr=missing,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return SkillResult(
                status="FAILED",
                parsed_artifacts={"error": str(exc)},
                raw_stdout="",
                raw_stderr=str(exc),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
