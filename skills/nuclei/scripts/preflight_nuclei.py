from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_ROOT_PF = Path(__file__).resolve().parent.parent
_COMMON_PF = _ROOT_PF / "common"
if _COMMON_PF.is_dir() and str(_COMMON_PF) not in sys.path:
    sys.path.insert(0, str(_COMMON_PF))
try:
    from workspace_resolve import resolve_under_workspace
except ImportError:
    resolve_under_workspace = None  # type: ignore[misc, assignment]


def _normalize_framework_hint(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    aliases = {
        "apache struts": "struts2",
        "struts": "struts2",
        "struts2": "struts2",
        "springboot": "spring",
        "spring boot": "spring",
        "spring": "spring",
        "thinkphp": "thinkphp",
        "jenkins": "jenkins",
        "jenkins ci": "jenkins",
        "elasticsearch": "elasticsearch",
        "elastic": "elasticsearch",
        "solr": "solr",
        "apache solr": "solr",
        "weblogic": "weblogic",
        "oracle weblogic": "weblogic",
        "xxl-job": "xxl-job",
        "xxljob": "xxl-job",
        "xxl_job": "xxl-job",
        "nacos": "nacos",
        "shiro": "shiro",
        "apache shiro": "shiro",
        "hadoop": "hadoop",
        "apache hadoop": "hadoop",
        "yarn": "hadoop",
        "hadoop yarn": "hadoop",
    }
    return aliases.get(s, s)


def _base_template_root() -> Path:
    # 优先容器内路径，其次源码路径（本地调试）
    candidates = [
        Path("/skill/temlists"),
        Path(__file__).resolve().parent.parent / "temlists",
    ]
    for p in candidates:
        if p.exists() and p.is_dir():
            return p
    return candidates[-1]


def _pick_template_path(framework_hint: str) -> tuple[Path, bool]:
    root = _base_template_root()
    fw = _normalize_framework_hint(framework_hint)
    if fw:
        scoped = root / fw
        if scoped.exists() and scoped.is_dir():
            return scoped, True
    return root, False


def _pick_target_list(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    def _candidates(raw_dir: str) -> list[Path]:
        out: list[Path] = []
        s = (raw_dir or "").strip()
        if not s:
            return out
        # 统一斜杠与大小写，增强 Windows → 容器路径推断的稳定性
        norm = s.replace("\\", "/")
        low = norm.lower()

        if resolve_under_workspace is not None:
            try:
                rp = resolve_under_workspace(s)
                if rp is not None:
                    out.append(rp)
            except Exception:
                pass

        p = Path(norm)
        out.append(p)

        # 兼容本地 orchestrator（Windows 路径）-> 容器内 executor（Linux 挂载）场景
        # 例如: D:\...\trustguard-agent\data\evidence-workspace\task-xxx\web-vuln\...\discovery
        # 需要映射到: /data/workspace/task-xxx/web-vuln/.../discovery
        container_ws = os.getenv("NUCLEI_SHARED_WORKSPACE_ROOT", "/data/workspace").strip() or "/data/workspace"
        markers = ("data/evidence-workspace/", "data/executor-workspace/", "evidence-workspace/", "executor-workspace/")
        suffix = ""
        for mk in markers:
            idx = low.find(mk)
            if idx >= 0:
                suffix = norm[idx + len(mk):].lstrip("/")
                break
        if suffix:
            out.append(Path(container_ws) / suffix)

        # 如果本身已是 /data/workspace 相对或绝对，也加一次标准化候选
        if "/data/workspace/" in low:
            pos = low.find("/data/workspace/")
            sub = norm[pos + len("/data/workspace/"):].lstrip("/")
            out.append(Path(container_ws) / sub)

        # 去重保序
        uniq: list[Path] = []
        seen: set[str] = set()
        for item in out:
            k = str(item)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(item)
        return uniq

    discovery_dir = str(params.get("output_discovery_dir") or params.get("output_dir") or "").strip()
    diagnostics: dict[str, Any] = {
        "target_list_source": "",
        "target_list_candidates": [],
        "target_list_readable": False,
    }
    all_bases: list[Path] = []
    all_bases.extend(_candidates(discovery_dir))
    seen_bases: set[str] = set()
    for base in all_bases:
        sk = str(base)
        if sk in seen_bases:
            continue
        seen_bases.add(sk)
        diagnostics["target_list_candidates"].append(sk)
        clustered = base / "clustered_targets.txt"
        if clustered.exists() and clustered.is_file():
            try:
                lines = [ln.strip() for ln in clustered.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            except Exception:
                lines = []
            if any(ln.startswith(("http://", "https://")) for ln in lines):
                diagnostics["target_list_source"] = "clustered"
                diagnostics["target_list_readable"] = True
                return str(clustered), diagnostics
        fallback = base / "katana_urls.txt"
        if fallback.exists() and fallback.is_file():
            try:
                lines = [ln.strip() for ln in fallback.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            except Exception:
                lines = []
            if any(ln.startswith(("http://", "https://")) for ln in lines):
                diagnostics["target_list_source"] = "katana"
                diagnostics["target_list_readable"] = True
                return str(fallback), diagnostics
    return "", diagnostics


def _normalize_url_for_targets(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        s = f"http://{s}"
    try:
        p = urlsplit(s)
        path = p.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        return urlunsplit((p.scheme or "http", p.netloc, path, "", ""))
    except Exception:
        return s


def _struts2_depth_candidates(single_url: str) -> list[str]:
    base = _normalize_url_for_targets(single_url)
    if not base:
        return []
    try:
        p = urlsplit(base)
    except Exception:
        return [base]

    host = f"{p.scheme}://{p.netloc}"
    seeds = [
        host + "/",
        host + "/index.action",
        host + "/actionChain1.action",
        host + "/struts2-showcase/",
        host + "/struts2-showcase/index.action",
        host + "/struts2-showcase/actionChain1.action",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for x in seeds:
        k = x.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def _sanitize_struts2_seed_url(raw: str) -> str:
    s = _normalize_url_for_targets(raw)
    if not s:
        return ""
    try:
        p = urlsplit(s)
    except Exception:
        return s
    path = p.path or "/"
    low = path.lower()
    # S2-057 namespace 注入必须落在目录层，不允许以 .action 结尾作为种子。
    if low.endswith(".action"):
        # 如果包含 /struts2-showcase/...，锚定到 showcase 根目录；否则回退站点根。
        marker = "/struts2-showcase/"
        idx = low.find(marker)
        if idx >= 0:
            path = path[: idx + len(marker)]
        else:
            path = "/"
    # 统一去双斜杠
    while "//" in path:
        path = path.replace("//", "/")
    if not path.startswith("/"):
        path = "/" + path
    return urlunsplit((p.scheme or "http", p.netloc, path, "", ""))


def _build_fallback_target_list(single_url: str, framework_hint: str) -> str:
    fw = _normalize_framework_hint(framework_hint)
    url = _normalize_url_for_targets(single_url)
    if not url:
        return ""
    candidates = [url]
    if fw == "struts2":
        candidates = _struts2_depth_candidates(_sanitize_struts2_seed_url(url))
    out_dir = Path(os.getenv("NUCLEI_FALLBACK_TARGET_DIR", "/tmp")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "nuclei_fallback_targets.txt"
    out_file.write_text("\n".join(candidates) + "\n", encoding="utf-8")
    return str(out_file)


def _tags_for_nuclei_cli(params: dict[str, Any], use_mode: str) -> str:
    """优先使用编排器下发的 tags/nuclei_tags（并集），否则回退到 scan/exploit 基线。"""
    raw = params.get("nuclei_tags")
    if raw is None:
        raw = params.get("tags")
    # 兼容被误序列化为字符串的 JSON 数组（如 '["struts","rce"]'）
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                import json

                parsed = json.loads(s)
                if isinstance(parsed, list):
                    raw = parsed
            except Exception:
                # 解析失败则按普通字符串继续走后面的逻辑
                pass
    if isinstance(raw, list) and raw:
        joined = ",".join(str(x).strip() for x in raw if str(x).strip())
        if joined:
            return joined
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "safe-poc" if use_mode == "scan" else "exploit"


def prepare_nuclei_command(
    *,
    mode: str,
    params: dict[str, Any],
    default_rate_limit: int,
    auth_headers: list[tuple[str, str]],
    user_agent: str,
    json_export_path: str,
) -> tuple[list[str], dict[str, Any]]:
    framework_hint = str(params.get("framework_hint") or params.get("framework_target") or "").strip()
    template_path, is_scoped = _pick_template_path(framework_hint)
    target_list_file, target_list_diag = _pick_target_list(params)
    use_mode = (mode or "").strip().lower()
    if use_mode not in ("scan", "exploit"):
        use_mode = "scan"

    cmd: list[str] = [
        "nuclei",
        "-t",
        str(template_path),
        "-jsonl",
        # 仅由外层 Python 将 stdout 写入 jsonl 文件，避免与 nuclei 自身 -json-export 对同一文件并发写入导致损坏
        "-rl",
        str(max(1, int(params.get("rate_limit") or default_rate_limit))),
    ]
    # 默认基线为 safe-poc / exploit；若编排器已合并垂直标签（如 struts,rce），则透传至 -tags
    # 实地证据 (task-92c2b41b... thinkphp 5.0.23): 模板目录已按 framework 收窄 (is_scoped=True)
    # 但编排器默认下发 params.nuclei_tags=["safe-poc"]，导致 nuclei -tags safe-poc 与模板 tags
    # (thinkphp,rce,cve) 不相交，输出 "no templates provided for scan"，0 命中。
    # 此时 -t /skill/temlists/<framework> 已经是安全的范围约束，-tags 不再必要；
    # 仅在编排器显式声明了非基线 tags（如 struts,rce,cve）时才保留过滤，继续支撑 safe/exploit 分流。
    _tags_cli = _tags_for_nuclei_cli(params, use_mode)
    _baseline_tag = "safe-poc" if use_mode == "scan" else "exploit"
    _skip_tags_filter = is_scoped and _tags_cli.strip().lower() == _baseline_tag
    if not _skip_tags_filter:
        cmd.extend(["-tags", _tags_cli])

    single_url = str(params.get("single_url") or params.get("url") or params.get("target") or "").strip()
    if _normalize_framework_hint(framework_hint) == "struts2" and single_url:
        single_url = _sanitize_struts2_seed_url(single_url)
    fallback_target_list_file = ""
    if not target_list_file and single_url:
        fallback_target_list_file = _build_fallback_target_list(single_url, framework_hint)
        if fallback_target_list_file:
            target_list_diag["target_list_source"] = "fallback"
            target_list_diag["target_list_readable"] = True

    if target_list_file:
        cmd.extend(["-l", target_list_file])
    elif fallback_target_list_file:
        cmd.extend(["-l", fallback_target_list_file])
    else:
        if single_url:
            cmd.extend(["-u", single_url])

    for k, v in auth_headers:
        cmd.extend(["-H", f"{k}: {v}"])
    if user_agent:
        cmd.extend(["-H", f"User-Agent: {user_agent}"])

    meta = {
        "framework_hint": _normalize_framework_hint(framework_hint),
        "template_root": str(_base_template_root()),
        "template_path": str(template_path),
        "template_scoped": is_scoped,
        "target_list_file": target_list_file,
        "fallback_target_list_file": fallback_target_list_file,
        "target_list_diagnostics": target_list_diag,
        "mode": use_mode,
    }
    return cmd, meta
