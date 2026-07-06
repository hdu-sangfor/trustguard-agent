"""
通用 passthrough 参数构建器：将 LLM 在 PlanItem.metadata.params 中写的技能级参数
按白名单透传到 CompiledInstruction.params。

背景：plan_execution_dispatch.py 在派发前会向 params 注入 target/timeout；
而 skill_rules/__init__.py 原本只为 nmap 等少数技能注册了 builder，
其余技能（如 read_workspace_artifact、dispatcher、nuclei）返回 {}，
导致 LLM 指定的 artifact_ref / run_id / framework_hint 等字段全部被丢弃。

本模块为常用技能提供收敛的白名单，避免 LLM 自由参数与工具 Pydantic 契约冲突。
"""
from __future__ import annotations

from typing import Any

from app.plan_models import PlanItem

# skill_id -> 允许透传的参数键集合（仅包含工具侧 execute.py 实际读取的字段）。
# 注意：target 与 timeout 由 plan_execution_dispatch 统一注入，无需列入白名单。
_PASSTHROUGH_WHITELIST_BY_SKILL: dict[str, frozenset[str]] = {
    # 证据读取 —— 读工作区 artifact
    "read_workspace_artifact": frozenset({
        "artifact_ref",
        "grep_keyword",
        "max_chars",
        "list_file_max_chars",
        "url_preview_max",
        "workspace_dir",
    }),
    # 证据读取 —— 读 URL 列表
    "read_target_list": frozenset({
        "rel_path",
        "path",
        "max_urls",
        "workspace_dir",
    }),
    # 爬虫/分片调度
    "dispatcher": frozenset({
        "operation",
        "run_id",
        "target_url",
        "url",
        "scan_url",
        "endpoint",
        "output_discovery_dir",
        "output_dir",
        "max_urls",
        "chunk_size",
        "seed_urls",
        "host_alias_map",
        "allow_scope_subdomains",
        "scope_hosts",
        "scope_allowlist",
        "katana_deny_patterns",
        "drop_unresolved_hosts",
        "manifest_ttl_seconds",
        "auth_header",
        "user_agent",
        "tags",
        "nuclei_tags",
    }),
    # 漏洞扫描
    "nuclei": frozenset({
        "run_id",
        "single_url",
        "url",
        "framework_hint",
        "nuclei_tags",
        "nuclei_timeout",
        "rate_limit",
        "severity",
        "output_discovery_dir",
    }),
    # 爬虫
    "katana": frozenset({
        "run_id",
        "output_dir",
        "katana_depth",
        "katana_concurrency",
        "katana_crawl_duration",
        "katana_js_crawl",
        "katana_max_assets",
        "katana_max_path_depth",
        "katana_skip_initial_target",
        "extra_seeds",
        "skip_dirsearch",
    }),
    # 目录扫描
    "dirsearch": frozenset({
        "output_dir",
        "extensions",
        "threads",
        "max_rate",
        "keywords",
        "enable_recursion",
        "max_total_lines",
    }),
    # 基础 HTTP 探测
    "http-enum": frozenset({
        "url",
        "max_time",
    }),
    "httpx": frozenset({
        "url",
        "threads",
        "rate_limit",
        "max_redirects",
    }),
    "ehole": frozenset({
        "url",
        "threads",
    }),
    # 精准 HTTP 请求（EXPLOIT 阶段主力）
    "curl-raw": frozenset({
        "url",
        "method",
        "headers",
        "body",
        "data",
        "form",
        "follow_redirects",
        "max_redirects",
        "insecure",
        "content_type",
        "user_agent",
    }),
    # 联网搜索
    "baidu-search": frozenset({
        "query",
        "q",
        "limit",
    }),
    # V1 Micro Sample（Pydantic extra=ignore 后其实无需白名单，但为一致性仍列出）
    "v1-micro-sample": frozenset({
        "note",
    }),
}


def build_passthrough_params(plan_item: PlanItem) -> dict[str, Any]:
    """
    从 PlanItem.metadata.params（或 metadata）按白名单提取技能级参数。
    同时兼容 metadata.<skill_id>_params 命名方式（与 nmap builder 的 metadata.nmap_params 惯例一致）。
    """
    sid = (plan_item.skill_id or "").strip().lower()
    whitelist = _PASSTHROUGH_WHITELIST_BY_SKILL.get(sid)
    if not whitelist:
        return {}

    meta = plan_item.metadata if isinstance(plan_item.metadata, dict) else {}
    if not meta:
        return {}

    # 支持的参数来源，按优先级由高到低合并：
    #   metadata.<skill_id>_params > metadata.params > metadata 顶层白名单字段
    candidate_blobs: list[dict[str, Any]] = []
    sid_key = f"{sid.replace('-', '_')}_params"
    if isinstance(meta.get(sid_key), dict):
        candidate_blobs.append(meta[sid_key])
    if isinstance(meta.get("params"), dict):
        candidate_blobs.append(meta["params"])
    # 顶层兜底：仅当 metadata 顶层直接包含白名单 key 时才采纳
    top_level = {k: v for k, v in meta.items() if k in whitelist}
    if top_level:
        candidate_blobs.append(top_level)

    out: dict[str, Any] = {}
    # 倒序合并，使高优先级源最后写入（覆盖低优先级）
    for blob in reversed(candidate_blobs):
        for k, v in blob.items():
            if k in whitelist:
                out[k] = v
    return out


def get_passthrough_skill_ids() -> tuple[str, ...]:
    """供 __init__.py 注册 builder 时遍历。"""
    return tuple(_PASSTHROUGH_WHITELIST_BY_SKILL.keys())
