"""
nuclei: Nuclei with -l chunk file only. Updates manifest chunk status.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
_COMMON = _ROOT / "common"
if _COMMON.is_dir() and str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))

from nuclei_io import extract_strict_key_findings, summarize_nuclei_jsonl  # noqa: E402
from workspace_manifest import load_manifest, new_run_id, run_root, safe_task_id, save_manifest  # noqa: E402
from postflight_nuclei import enrich_postflight_artifacts  # noqa: E402
from etl import normalize_scope_hostname, scope_hosts_for_payload  # noqa: E402
from preflight_nuclei import prepare_nuclei_command  # noqa: E402


def _build_llm_ready_artifacts(artifacts: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 nuclei 汇总结果中提取给 LLM 使用的瘦身结构。
    统一 schema：
      - vulnerabilities: list[dict]
      - severity_histogram: dict
      - tech_stack_evidence: list[str] (最多 20 条)
      - raw_preview: list[str]
      - diagnostics: dict（至少带 nuclei_rc）
    任何缺失/类型不符字段都提供安全默认值，避免空指针。
    """
    src = artifacts or {}

    vulns_raw = src.get("vulnerabilities") or []
    if isinstance(vulns_raw, list):
        vulnerabilities: List[Dict[str, Any]] = [
            v for v in vulns_raw if isinstance(v, dict)
        ]
    else:
        vulnerabilities = []

    hist = src.get("severity_histogram")
    severity_histogram: Dict[str, Any] = hist if isinstance(hist, dict) else {}

    tse_raw = src.get("tech_stack_evidence") or []
    tech_stack_evidence: List[str] = []
    if isinstance(tse_raw, list):
        for item in tse_raw:
            s = str(item or "").strip()
            if s:
                tech_stack_evidence.append(s)
            if len(tech_stack_evidence) >= 20:
                break

    raw_preview_raw = src.get("raw_preview") or []
    raw_preview: List[str] = []
    if isinstance(raw_preview_raw, list):
        for item in raw_preview_raw:
            s = str(item or "").strip()
            if s:
                raw_preview.append(s[:500])
    elif isinstance(raw_preview_raw, str) and raw_preview_raw.strip():
        raw_preview.append(raw_preview_raw.strip()[:500])

    diag = src.get("diagnostics")
    diagnostics: Dict[str, Any] = diag if isinstance(diag, dict) else {}
    if "nuclei_rc" not in diagnostics:
        # direct/manifest 路径都会在 diagnostics 中写入 nuclei_rc，这里兜底一次
        rc_val = src.get("nuclei_rc")
        if isinstance(rc_val, int):
            diagnostics["nuclei_rc"] = rc_val

    return {
        "vulnerabilities": vulnerabilities,
        "severity_histogram": severity_histogram,
        "tech_stack_evidence": tech_stack_evidence,
        "raw_preview": raw_preview,
        "diagnostics": diagnostics,
    }


def _nuclei_tags_param_to_str(raw: Any) -> str:
    """编排器可能下发 list[str]；CLI 仅接受逗号分隔字符串。"""
    if isinstance(raw, list):
        return ",".join(str(x).strip() for x in raw if str(x).strip())
    return str(raw or "").strip()


def emit_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _run_stdout_file(cmd: list[str], out_path: Path, timeout: int) -> tuple[int, str]:
    try:
        with open(out_path, "wb") as outf:
            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=outf,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        return proc.returncode, err
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -1, str(e)


def _has_nuclei_template_files(dir_path: str) -> bool:
    p = Path(dir_path)
    if not p.exists() or not p.is_dir():
        return False
    checked = 0
    for root, _, files in os.walk(p):
        for name in files:
            checked += 1
            if name.endswith((".yaml", ".yml")):
                return True
            if checked >= 5000:
                return False
    return False


def _resolve_nuclei_templates_dir() -> str | None:
    """唯一允许的模板根：temlists（与 preflight `-t` 一致）。禁止回退到全量 nuclei-templates。"""
    for p in (
        os.getenv("NUCLEI_TEMPLATES_DIR", "").strip(),
        "/skill/temlists",
        str(Path(__file__).resolve().parent.parent / "temlists"),
    ):
        if p and _has_nuclei_template_files(p):
            return str(Path(p).resolve())
    return None


def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as e:
        return -1, "", str(e)


def _ensure_nuclei_templates(timeout: int = 90) -> tuple[str | None, dict[str, Any]]:
    """仅校验 temlists 内存在可用 YAML；不执行 nuclei -update-templates（避免拉取官方全量库）。"""
    _ = timeout
    diag: dict[str, Any] = {"templates_policy": "temlists_only"}
    tdir = _resolve_nuclei_templates_dir()
    if tdir:
        diag["templates_ready"] = True
        diag["templates_dir"] = tdir
        return tdir, diag

    diag["templates_ready"] = False
    diag["templates_error"] = "no .yaml/.yml under NUCLEI_TEMPLATES_DIR or /skill/temlists"
    return None, diag


def _parse_header_lines(auth_header: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in (auth_header or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k:
                out.append((k, v))
    return out


def _coerce_nuclei_rate_limit(raw: Any, default: int = 50) -> tuple[int, str | None]:
    """
    将下发的 rate_limit 规范为可用整数，避免 int("low") 等崩溃。
    兼容 low/medium/high 语义值，未知值回退 default。
    """
    if raw is None:
        return max(1, int(default)), None
    if isinstance(raw, bool):
        return max(1, int(default)), "invalid_bool"
    if isinstance(raw, (int, float)):
        return max(1, int(raw)), None
    s = str(raw).strip().lower()
    if not s:
        return max(1, int(default)), None
    preset = {
        "low": 10,
        "slow": 10,
        "medium": 30,
        "normal": 50,
        "high": 80,
        "fast": 100,
    }
    if s in preset:
        return preset[s], f"preset:{s}"
    try:
        return max(1, int(float(s))), None
    except (ValueError, TypeError):
        return max(1, int(default)), f"fallback_default_from:{s}"


def _validate_url_lines(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, "chunk file missing"
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except Exception as e:
        return False, str(e)
    if not lines:
        return False, "chunk file empty (refusing nuclei without URL list)"
    from urllib.parse import urlparse

    for ln in lines:
        if not ln.startswith(("http://", "https://")):
            return False, f"invalid URL line: {ln[:80]}"
        try:
            urlparse(ln)
        except Exception:
            return False, f"unparseable URL: {ln[:80]}"
    return True, ""


def _resolve_rr(ws: str, task_id: str, run_id: str, executor_artifact_base: str) -> Path:
    rr = run_root(ws, task_id, run_id)
    if executor_artifact_base:
        try:
            p = Path(executor_artifact_base)
            if not p.is_absolute():
                p = Path(ws) / p
            if len(p.parts) >= 4:
                task_root = p.parents[2]
                rr = task_root / "web-vuln" / run_id
        except Exception:
            pass
    return rr


def _to_ws_rel(path: Path, ws: str) -> str:
    try:
        return path.resolve().relative_to(Path(ws).resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _run_direct_scan(payload: dict[str, Any], start: float) -> int:
    """
    nuclei_scan_mode=direct：对单 URL 执行 nuclei -u（不依赖 manifest/chunk）。
    用于仅有指纹或端点 URL、尚无 katana 分片时的动态打击。
    """
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    task_id = safe_task_id(str(payload.get("task_id") or "local"))
    run_id = str(params.get("run_id") or "").strip() or new_run_id()
    single_url = str(
        params.get("single_url") or params.get("url") or payload.get("target") or params.get("target") or ""
    ).strip()
    if not single_url.startswith(("http://", "https://")):
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "direct mode requires http(s) single_url"},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    if str(payload.get("allowed_target") or "").strip():
        ch = scope_hosts_for_payload(single_url, payload)
        uh = normalize_scope_hostname(urlparse(single_url).hostname or "")
        if not uh or uh not in ch:
            emit_result(
                {
                    "status": "FAILED",
                    "parsed_artifacts": {
                        "error": "direct nuclei URL host not in task scope (target/allowed_target)",
                        "single_url": single_url,
                        "allowed_hosts": sorted(ch),
                    },
                    "raw_stdout": "",
                    "raw_stderr": "scope guard rejected single_url",
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                }
            )
            return 1

    ws = os.environ.get("WORKSPACE_ROOT", "/tmp")
    pre_base = str(context.get("executor_artifact_base") or "").strip()
    rr = _resolve_rr(ws, task_id, run_id, pre_base)
    rr.mkdir(parents=True, exist_ok=True)
    rd = rr / "results"
    rd.mkdir(parents=True, exist_ok=True)
    out_path = rd / "chunk_0001.jsonl"

    auth_header = str(params.get("auth_header") or context.get("auth_header") or "").strip()
    user_agent = str(params.get("user_agent") or context.get("user_agent") or "").strip()
    # 注意：编排器可能同时下发 params.tags（用于其它含义）和 params.nuclei_tags（用于 nuclei -tags）。
    # 这里优先使用 nuclei_tags，避免错误加载大量模板导致线程耗尽。
    tags_source = (
        params.get("nuclei_tags")
        or params.get("nuclei_tags_requested")
        or params.get("tags")
        or context.get("nuclei_tags")
        or ""
    )
    tags_s = _nuclei_tags_param_to_str(tags_source).strip()
    tags_defaulted = False
    if not tags_s:
        tags_s = "cve,tech,exposure"
        tags_defaulted = True

    budget = int(params.get("timeout") or params.get("nuclei_timeout") or 120)
    nuc_rl, rl_note = _coerce_nuclei_rate_limit(params.get("rate_limit"), default=50)
    no_interactsh = str(params.get("no_interactsh") or "").lower() in ("1", "true", "yes") or str(
        os.getenv("NUCLEI_NO_INTERACTSH") or ""
    ).lower() in ("1", "true", "yes")

    template_dir, template_diag = _ensure_nuclei_templates(timeout=min(90, budget // 2))
    if not template_dir:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {
                    "error": template_diag.get("templates_error") or "temlists template root missing or empty",
                    "diagnostics": template_diag,
                },
                "raw_stdout": "",
                "raw_stderr": str(template_diag.get("templates_error") or ""),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    phase_raw = str((params.get("mode") or context.get("phase") or "")).strip().upper()
    mode = "exploit" if phase_raw == "EXPLOIT" or str(params.get("mode") or "").strip().lower() == "exploit" else "scan"
    preflight_params = dict(params)
    preflight_params.setdefault("single_url", single_url)
    preflight_params.setdefault("target", single_url)
    preflight_params.setdefault("framework_hint", context.get("framework_target") or context.get("framework_hint") or "")
    nuc_cmd, preflight_meta = prepare_nuclei_command(
        mode=mode,
        params=preflight_params,
        default_rate_limit=nuc_rl,
        auth_headers=_parse_header_lines(auth_header),
        user_agent=user_agent,
        json_export_path=str(out_path),
    )
    # 补充执行稳定参数
    nuc_cmd.extend(["-duc", "-silent", "-nc"])

    if no_interactsh:
        nuc_cmd.append("-ni")

    extra = params.get("extra")
    if isinstance(extra, list):
        nuc_cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        nuc_cmd.extend(shlex.split(extra))

    rc, err_n = _run_stdout_file(nuc_cmd, out_path, timeout=budget)
    diag = dict(template_diag)
    diag["nuclei_scan_mode"] = "direct"
    diag["preflight"] = preflight_meta
    diag["single_url"] = single_url
    if rl_note:
        diag["rate_limit_normalized"] = rl_note
    diag["rate_limit_effective"] = nuc_rl
    if tags_defaulted:
        diag["nuclei_tags_defaulted"] = True
    diag["nuclei_rc"] = rc
    if rc != 0 and err_n:
        diag["nuclei_stderr"] = (err_n or "")[:2000]

    vulns, hist, low_info, tech_stack_evidence = summarize_nuclei_jsonl(out_path)
    strict_capture = extract_strict_key_findings(out_path, max_findings=60)
    pre_wsref = str(context.get("executor_artifact_ref") or "").strip()
    status = "SUCCESS" if rc == 0 else ("TIMEOUT" if rc == -1 else "FAILED")
    artifacts = {
        "run_id": run_id,
        "nuclei_scan_mode": "direct",
        "single_url": single_url,
        "severity_histogram": hist,
        "low_info_count": low_info,
        "tech_stack_evidence": tech_stack_evidence[:20],
        "vulnerabilities": vulns,
        "key_findings": strict_capture.get("key_findings") or [],
        "high_value_facts": strict_capture.get("high_value_facts") or [],
        "partial_results": rc != 0,
        "diagnostics": diag,
        "workspace_artifact_ref": pre_wsref or _to_ws_rel(rr, ws),
        "result_path": "results/chunk_0001.jsonl",
    }
    artifacts = enrich_postflight_artifacts(artifacts, mode=mode, result_path=out_path)
    artifacts["llm_ready"] = _build_llm_ready_artifacts(artifacts)
    emit_result(
        {
            "status": status,
            "parsed_artifacts": artifacts,
            "raw_stdout": "",
            "raw_stderr": (err_n or "")[:4000],
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
    )
    return 0 if rc == 0 else 1


def main() -> int:
    start = time.perf_counter()
    raw_argv = sys.argv[1] if len(sys.argv) > 1 and str(sys.argv[1]).strip() else "{}"
    try:
        payload = json.loads(raw_argv)
    except Exception:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "invalid JSON argv"},
                "raw_stdout": "",
                "raw_stderr": "invalid json",
                "duration_ms": 0,
            }
        )
        return 1

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}

    scan_mode = str(params.get("nuclei_scan_mode") or "").strip().lower()
    # Also check payload.target as fallback (orchestrator sends target at top level, not params.single_url)
    single_for_direct = str(
        params.get("single_url") or params.get("url") or payload.get("target") or params.get("target") or ""
    ).strip()
    chunk_idx_param = int(params.get("chunk_index") or 0)
    chunk_path_param_early = str(params.get("chunk_path") or "").strip()
    if scan_mode == "direct":
        return _run_direct_scan(payload, start)
    # 兼容：仅传 target URL、无 run_id/manifest 时走 direct，避免强依赖 katana 分片
    if (
        single_for_direct.startswith(("http://", "https://"))
        and not chunk_idx_param
        and not chunk_path_param_early
        and not str(params.get("run_id") or "").strip()
    ):
        return _run_direct_scan(payload, start)

    task_id = safe_task_id(str(payload.get("task_id") or "local"))
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "params.run_id required"},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    chunk_index = int(params.get("chunk_index") or 0)
    chunk_path_param = str(params.get("chunk_path") or "").strip().replace("\\", "/")

    ws = os.environ.get("WORKSPACE_ROOT", "/tmp")
    pre_base = str(context.get("executor_artifact_base") or "").strip()
    rr = _resolve_rr(ws, task_id, run_id, pre_base)

    m = load_manifest(rr)
    if not m:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "manifest.json not found; run dispatcher prepare first"},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    ctx_block = m.get("context") if isinstance(m.get("context"), dict) else {}
    auth_header = str(params.get("auth_header") or ctx_block.get("auth_header") or "").strip()
    user_agent = str(params.get("user_agent") or ctx_block.get("user_agent") or "").strip()
    tags_s = _nuclei_tags_param_to_str(
        params.get("nuclei_tags")
        or params.get("nuclei_tags_requested")
        or params.get("tags")
        or ctx_block.get("nuclei_tags")
        or ""
    ).strip()
    tags_defaulted = False
    if not tags_s:
        # 与 SKILL.md 对齐：VULN_SCAN 默认 safe-poc，EXPLOIT 默认 exploit；
        # 历史默认 "cve,tech,exposure" 不含 safe-poc，导致 s2-045 等自定义模板被过滤（0 命中）
        phase_raw_m = str((params.get("mode") or context.get("phase") or ctx_block.get("phase") or "")).strip().upper()
        if phase_raw_m == "EXPLOIT" or str(params.get("mode") or "").strip().lower() == "exploit":
            tags_s = "exploit"
        else:
            tags_s = "safe-poc"
        tags_defaulted = True

    chunk_file: Path | None = None
    result_rel: str = ""
    if chunk_path_param:
        chunk_file = rr / chunk_path_param.replace("/", os.sep)
        for ch in m.get("chunks") or []:
            if isinstance(ch, dict) and str(ch.get("path") or "").replace("\\", "/") == chunk_path_param:
                chunk_index = int(ch.get("index") or 0)
                result_rel = str(ch.get("result_path") or "")
                break
    elif chunk_index > 0:
        for ch in m.get("chunks") or []:
            if isinstance(ch, dict) and int(ch.get("index") or 0) == chunk_index:
                rel = str(ch.get("path") or "")
                if rel:
                    chunk_file = rr / rel.replace("/", os.sep)
                    result_rel = str(ch.get("result_path") or "")
                break
    else:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": "params.chunk_index (1-based) or chunk_path required"},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    if chunk_file is None or not chunk_file.exists():
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": f"chunk file not found: {chunk_file}"},
                "raw_stdout": "",
                "raw_stderr": "",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    ok, err = _validate_url_lines(chunk_file)
    if not ok:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {"error": f"nuclei guard: {err}"},
                "raw_stdout": "",
                "raw_stderr": err,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    if not result_rel and chunk_index > 0:
        for ch in m.get("chunks") or []:
            if isinstance(ch, dict) and int(ch.get("index") or 0) == chunk_index:
                result_rel = str(ch.get("result_path") or "")
                break

    if not result_rel:
        result_rel = f"results/chunk_{chunk_index:04d}.jsonl"

    out_path = rr / result_rel.replace("/", os.sep)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 与 direct 扫描默认 120s 对齐；manifest 多标签 + 全量模板时 60s 极易误杀（见 diagnostics.nuclei_stderr）
    budget = int(params.get("timeout") or params.get("nuclei_timeout") or 120)
    nuc_rl, rl_note = _coerce_nuclei_rate_limit(params.get("rate_limit"), default=50)
    no_interactsh = str(params.get("no_interactsh") or "").lower() in ("1", "true", "yes") or str(
        os.getenv("NUCLEI_NO_INTERACTSH") or ""
    ).lower() in ("1", "true", "yes")

    template_dir, template_diag = _ensure_nuclei_templates(timeout=min(90, budget // 2))
    if not template_dir:
        emit_result(
            {
                "status": "FAILED",
                "parsed_artifacts": {
                    "error": template_diag.get("templates_error") or "temlists template root missing or empty",
                    "diagnostics": template_diag,
                    "chunk_index": chunk_index,
                },
                "raw_stdout": "",
                "raw_stderr": str(template_diag.get("templates_error") or ""),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        return 1

    nuc_cmd: list[str] = [
        "nuclei",
        "-l",
        str(chunk_file),
        "-t",
        template_dir,
        "-duc",
        "-silent",
        "-nc",
        "-jsonl",
        "-severity",
        "critical,high,medium",
        "-rl",
        str(nuc_rl),
    ]

    for k, v in _parse_header_lines(auth_header):
        nuc_cmd.extend(["-H", f"{k}: {v}"])
    if user_agent:
        nuc_cmd.extend(["-H", f"User-Agent: {user_agent}"])

    if no_interactsh:
        nuc_cmd.append("-ni")

    if tags_s:
        nuc_cmd.extend(["-tags", tags_s])

    extra = params.get("extra")
    if isinstance(extra, list):
        nuc_cmd.extend([str(x) for x in extra if str(x).strip()])
    elif isinstance(extra, str) and extra.strip():
        nuc_cmd.extend(shlex.split(extra))

    rc, err_n = _run_stdout_file(nuc_cmd, out_path, timeout=budget)
    diag = dict(template_diag)
    if tags_defaulted:
        diag["nuclei_tags_defaulted"] = True
    if rl_note:
        diag["rate_limit_normalized"] = rl_note
    diag["rate_limit_effective"] = nuc_rl
    diag["nuclei_rc"] = rc
    diag["chunk_index"] = chunk_index
    diag["chunk_file"] = str(chunk_file)
    if rc != 0 and err_n:
        diag["nuclei_stderr"] = (err_n or "")[:2000]

    vulns, hist, low_info, tech_stack_evidence = summarize_nuclei_jsonl(out_path)
    strict_capture = extract_strict_key_findings(out_path, max_findings=60)

    for ch in m.get("chunks") or []:
        if isinstance(ch, dict) and int(ch.get("index") or 0) == chunk_index:
            ch["status"] = "done" if rc == 0 else "failed"
            ch["nuclei_rc"] = rc
            break
    save_manifest(rr, m)

    pre_wsref = str(context.get("executor_artifact_ref") or "").strip()

    status = "SUCCESS" if rc == 0 else ("TIMEOUT" if rc == -1 else "FAILED")
    base_artifacts = {
        "run_id": run_id,
        "chunk_index": chunk_index,
        "severity_histogram": hist,
        "low_info_count": low_info,
        "tech_stack_evidence": tech_stack_evidence[:20],
        "vulnerabilities": vulns,
        "key_findings": strict_capture.get("key_findings") or [],
        "high_value_facts": strict_capture.get("high_value_facts") or [],
        "partial_results": rc != 0,
        "diagnostics": diag,
        "workspace_artifact_ref": pre_wsref or _to_ws_rel(rr, ws),
        "result_path": result_rel,
    }
    base_artifacts["llm_ready"] = _build_llm_ready_artifacts(base_artifacts)
    emit_result(
        {
            "status": status,
            "parsed_artifacts": base_artifacts,
            "raw_stdout": "",
            "raw_stderr": (err_n or "")[:4000],
            "duration_ms": int((time.perf_counter() - start) * 1000),
        }
    )
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
