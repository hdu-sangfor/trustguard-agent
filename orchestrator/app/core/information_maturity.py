"""
信息完备性等级 (Information Maturity Levels, IML) — 基于 target_context 事实推断 1–4 级。

用于编排器侧预检：按成熟度解锁/约束 nuclei、dispatcher、dirsearch 等 Web 流水线技能，
避免在缺乏事实依据时盲目调用扫描器。

可选：传入 workspace_root + task_id 时，对 Katana discovery 落盘做轻量 os.path.exists 校验，
缓解「上下文先更新、磁盘 artifact 尚未可见」导致的误判。

等级定义（与产品约定对齐）：
  L1: 基础连通 — IP/Domain + 端口或 HTTP 可达
  L2: 服务识别 — HTTP Server / Banner / 技术栈粗信号
  L3: 应用指纹 — 框架/CMS/中间件级指纹（如 Struts2）
  L4: 路径资产 — 具体端点/动作路径可用于精准打击

说明：IML 每个 tick 在 ensure_iml_in_context 中全量重算，不保留历史峰值；若出现负向连通信号
（如 target_unreachable、探测明确失败），可回退为 0 级。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.agent_tools import fingerprint_signals_present, get_fingerprint_text_for_pipeline
from app.core.http_enum_seeds import extract_http_enum_seed_urls, normalize_url_for_pipeline

# 用于判定 L3（框架级）的常见关键字（小写匹配）
def _safe_task_id(s: str) -> str:
    """与 workspace_manifest.safe_task_id 对齐的轻量清洗（避免编排器依赖 skills 包）。"""
    t = re.sub(r"[^a-zA-Z0-9_-]+", "_", (s or "task").strip())[:96]
    return t or "task"


def _katana_discovery_file(workspace_root: str, task_id: str, run_id: str) -> Path:
    return Path(workspace_root) / _safe_task_id(task_id) / "web-vuln" / run_id / "discovery" / "katana_urls.txt"


def _latest_task_katana_discovery_file(workspace_root: str, task_id: str) -> Path | None:
    base = Path(workspace_root) / _safe_task_id(task_id) / "web-vuln"
    if not base.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    try:
        for run_dir in base.iterdir():
            if not run_dir.is_dir():
                continue
            ku = run_dir / "discovery" / "katana_urls.txt"
            if ku.is_file() and ku.stat().st_size > 0:
                candidates.append((ku.stat().st_mtime, ku))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _katana_url_counts_trustworthy_fs(
    ctx: dict[str, Any],
    *,
    workspace_root: str | None,
    task_id: str | None,
    target: str,
) -> tuple[bool, dict[str, Any]]:
    """
    若上下文声明 katana 有 URL，但可解析到 run_id 时 discovery 文件尚不存在，则视为不可信（可能仅内存先更新）。
    无法解析 run_id 或未配置 workspace 时不做强校验（返回 True）。
    """
    meta: dict[str, Any] = {"fs_check": "skipped"}
    if not (workspace_root or "").strip() or not (task_id or "").strip():
        return True, meta
    u = ctx.get("katana_url_counts") or ctx.get("url_counts")
    if not isinstance(u, dict):
        return True, meta
    try:
        tot = int(u.get("total_raw") or 0)
    except Exception:
        tot = 0
    if tot <= 0:
        meta["fs_check"] = "not_applicable"
        return True, meta
    # 仅检查当前 task 目录（run_id 优先，缺失则回退到 task 下最近一次 katana 落盘）。
    workspace_root = workspace_root.strip()
    rid = str(ctx.get("web_vuln_run_id") or "").strip()
    run_ok = False
    run_path = None
    if rid:
        run_path = _katana_discovery_file(workspace_root, task_id or "", rid)
        run_ok = run_path.is_file()
    latest_path = _latest_task_katana_discovery_file(workspace_root, task_id or "")
    latest_ok = latest_path is not None and latest_path.is_file()

    ok = bool(run_ok or latest_ok)
    meta["fs_check"] = "performed"
    meta["katana_discovery_present"] = ok
    if run_path is not None:
        meta["katana_run_discovery_path"] = str(run_path)
    if latest_path is not None:
        meta["katana_task_latest_discovery_path"] = str(latest_path)
    return ok, meta


def _has_negative_connectivity_evidence(ctx: dict[str, Any] | None) -> bool:
    """
    目标侧负向信号：服务下线 / 探测明确失败时，应允许 IML 回退（与历史成功并存时优先采信负向）。
    仅在有明确字段时触发，避免未跑 http-enum 时误判。
    """
    if not isinstance(ctx, dict) or not ctx:
        return False
    if ctx.get("target_unreachable") is True:
        return True
    if ctx.get("probe_liveness_failed") is True:
        return True
    if ctx.get("last_http_probe_failed") is True:
        return True
    rc = ctx.get("http-enum_returncode")
    st = str(ctx.get("http-enum_http_status") or "").strip()
    if rc is not None and rc != 0:
        if not st or st in ("0", "000"):
            return True
    return False


_FRAMEWORK_HINTS = (
    "struts",
    "spring",
    "django",
    "flask",
    "laravel",
    "rails",
    "aspnet",
    "asp.net",
    "thinkphp",
    "weblogic",
    "websphere",
    "jenkins",
    "drupal",
    "wordpress",
    "joomla",
    "shiro",
    "fastjson",
    "log4j",
    "struts2",
)


def _text_blob(ctx: dict[str, Any] | None) -> str:
    if not isinstance(ctx, dict) or not ctx:
        return ""
    parts: list[str] = []
    for k, v in ctx.items():
        kl = str(k).lower()
        if any(
            s in kl
            for s in (
                "fingerprint",
                "whatweb",
                "banner",
                "header",
                "title",
                "stack",
                "raw_preview",
                "http-enum",
                "curl-raw",
            )
        ):
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, dict):
                try:
                    parts.append(str(v))
                except Exception:
                    pass
    fp = get_fingerprint_text_for_pipeline(ctx)
    if fp:
        parts.append(fp)
    return "\n".join(parts).lower()


def _has_connectivity_evidence(ctx: dict[str, Any] | None, target: str) -> bool:
    """L1：主机/端口可达或已明确 HTTP 服务。"""
    t = (target or "").strip()
    if t.startswith(("http://", "https://")):
        return True
    if not isinstance(ctx, dict):
        return False
    if ctx.get("http-enum_http_status") or ctx.get("http-enum_returncode") == 0:
        return True
    if str(ctx.get("http-enum_url") or "").strip().startswith(("http://", "https://")):
        return True
    nmap_ps = ctx.get("nmap_port_states") or ctx.get("nmap_open_ports")
    if isinstance(nmap_ps, dict) and nmap_ps:
        return True
    if isinstance(nmap_ps, list) and nmap_ps:
        return True
    # curl-raw / 其它探测成功
    if str(ctx.get("curl-raw_returncode") or "") == "0" or ctx.get("curl-raw_http_status"):
        return True
    return False


def _has_service_banner_evidence(ctx: dict[str, Any] | None) -> bool:
    """L2：HTTP Server / Banner 或等价指纹粗信号。"""
    if fingerprint_signals_present(ctx or {}):
        return True
    if not isinstance(ctx, dict):
        return False
    h = ctx.get("http-enum_headers")
    if isinstance(h, dict) and str(h.get("server") or h.get("Server") or "").strip():
        return True
    srv = ctx.get("headers") if isinstance(ctx.get("headers"), dict) else {}
    if isinstance(srv, dict) and str(srv.get("server") or srv.get("Server") or "").strip():
        return True
    if str(ctx.get("stack_hint") or "").strip():
        return True
    return False


def _has_framework_fingerprint(blob: str) -> bool:
    """L3：框架/应用级指纹。"""
    if not blob:
        return False
    return any(h in blob for h in _FRAMEWORK_HINTS)


def _has_endpoint_assets(
    ctx: dict[str, Any] | None,
    target: str,
    *,
    katana_counts_trustworthy: bool = True,
) -> bool:
    """L4：除根路径外的可扫描端点或高价值路径。"""
    if not isinstance(ctx, dict):
        return False
    seeds = extract_http_enum_seed_urls(ctx)
    if seeds:
        return True
    if katana_counts_trustworthy:
        for key in ("katana_url_counts", "url_counts", "dispatcher_high_value_endpoints"):
            v = ctx.get(key)
            if isinstance(v, dict):
                tot = v.get("total_raw") or v.get("katana") or v.get("total")
                if isinstance(tot, int) and tot > 0:
                    return True
            if isinstance(v, list) and len(v) > 0:
                return True
    # 任务目标自身带路径且非 /（先规范化 query，避免仅差缓存参数的 URL 误判）
    try:
        raw_t = (target or "").strip()
        if raw_t.startswith(("http://", "https://")):
            raw_t = normalize_url_for_pipeline(raw_t)
        p = urlparse(raw_t)
        path = (p.path or "").strip()
        if path and path != "/":
            return True
    except Exception:
        pass
    return False


def compute_information_maturity(
    target_context: dict[str, Any] | None,
    target: str,
    *,
    task_id: str | None = None,
    workspace_root: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """
    返回 (level, detail)。level ∈ {0,1,2,3,4}，0 表示尚无任何可用连通证据。
    每个 tick 全量重算，不读取历史 information_maturity_level；可选 workspace_root+task_id 做 Katana 落盘校验。
    """
    ctx = target_context if isinstance(target_context, dict) else {}
    detail: dict[str, Any] = {
        "target": (target or "").strip(),
        "iml_recomputed_each_tick": True,
    }
    ws = (workspace_root or os.getenv("ORCHESTRATOR_WORKSPACE_ROOT") or os.getenv("WORKSPACE_ROOT") or "").strip() or None

    if _has_negative_connectivity_evidence(ctx):
        detail["reason"] = "negative_connectivity_evidence"
        detail["level"] = 0
        return 0, detail

    blob = _text_blob(ctx)

    if not _has_connectivity_evidence(ctx, target):
        detail["reason"] = "no_connectivity_evidence"
        return 0, detail

    katana_ok, fs_meta = _katana_url_counts_trustworthy_fs(ctx, workspace_root=ws, task_id=task_id, target=target)
    detail.update(fs_meta)

    detail["l1_connectivity"] = True
    level = 1

    if _has_service_banner_evidence(ctx):
        detail["l2_service_banner"] = True
        level = 2
    else:
        detail["l2_service_banner"] = False

    if level >= 2 and (_has_framework_fingerprint(blob) or _has_framework_fingerprint((target or "").lower())):
        detail["l3_framework_fingerprint"] = True
        level = 3
    else:
        detail["l3_framework_fingerprint"] = False

    # L4：存在可扫描的显式端点（表单 action、多 URL 等）即可路径级打击；不要求先完成 L2/L3
    if _has_endpoint_assets(ctx, target, katana_counts_trustworthy=katana_ok) and level >= 1:
        detail["l4_endpoint_assets"] = True
        level = max(level, 4)
    else:
        detail["l4_endpoint_assets"] = False

    # 无 L2 但已有强框架标题（仅 http-enum_title）时抬到 L3
    if level < 3:
        title = str(ctx.get("http-enum_title") or ctx.get("title") or "").lower()
        if _has_framework_fingerprint(title):
            detail["l3_framework_fingerprint"] = True
            level = max(level, 3)

    detail["level"] = level
    return level, detail
