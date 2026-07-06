"""dirsearch：基于上下文指纹进行参数兜底补全（确定性防漏传）。"""
from __future__ import annotations

from typing import Any

from app.core.preflight_context import PreflightContext

JAVA_KEYWORDS = (
    "java",
    "tomcat",
    "servlet",
    "jsp",
    "jsessionid",
    "jboss",
    "weblogic",
    "struts",
    "spring",
)
STRUTS_KEYWORDS = ("struts", "struts2", ".action", "xwork")
SPRING_KEYWORDS = ("spring", "actuator", "whitelabel error page")


def _to_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _ctx_blob(ctx: dict[str, Any]) -> str:
    parts: list[str] = []

    def add(v: Any) -> None:
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, dict):
            for x in v.values():
                add(x)
        elif isinstance(v, list):
            for x in v[:200]:
                add(x)

    add(ctx.get("confirmed_facts"))
    add(ctx.get("entity_blackboard"))
    add(ctx.get("tech_stack_evidence"))
    add(ctx.get("pipeline_tech_stack_evidence"))
    add(ctx.get("fingerprint"))
    add(ctx.get("stack_hint"))
    add(ctx.get("history_summary"))
    add(ctx.get("raw_preview"))
    add(ctx.get("response_headers"))
    add(ctx.get("headers"))
    add(ctx.get("http_enum_raw_preview"))
    add(ctx.get("http_enum_response_headers"))
    return "\n".join(parts).lower()


def _merge_unique(base: list[str], patch: list[str]) -> list[str]:
    seen = {x.lower() for x in base}
    out = list(base)
    for x in patch:
        if x.lower() in seen:
            continue
        out.append(x)
        seen.add(x.lower())
    return out


def apply_dirsearch_preflight(params: dict[str, Any], pctx: PreflightContext) -> None:
    ctx = pctx.ctx
    params.setdefault("target", pctx.state.target)

    # 统一缺省策略，避免模型漏传导致执行参数失衡。
    # 速率参数由技能脚本底层固定接管，这里不再注入 rate_limit。
    params.setdefault("enable_recursion", False)

    ext = _to_list(params.get("extensions"))
    tags = _to_list(params.get("wordlist_tags"))
    suffixes = _to_list(params.get("fuzz_suffixes"))

    blob = _ctx_blob(ctx)

    java_hits = any(k in blob for k in JAVA_KEYWORDS)
    struts_hits = any(k in blob for k in STRUTS_KEYWORDS)
    spring_hits = any(k in blob for k in SPRING_KEYWORDS)

    if java_hits:
        tags = _merge_unique(tags, ["java_full"])
        if not ext:
            ext = _merge_unique(ext, ["jsp", "do", "action"])

    if struts_hits:
        tags = _merge_unique(tags, ["struts2", "api_fuzz"])
        if not ext:
            ext = _merge_unique(ext, ["action", "do"])
        params["enable_recursion"] = True
        suffixes = _merge_unique(suffixes, [".bak", "~", ".swp"])

    if spring_hits:
        tags = _merge_unique(tags, ["java_full", "api_fuzz"])
        if not ext:
            ext = _merge_unique(ext, ["jsp", "do", "action"])

    if not tags:
        tags = ["generic_full"]

    if ext:
        params["extensions"] = ext
    params["wordlist_tags"] = tags
    if suffixes:
        params["fuzz_suffixes"] = suffixes

    params["dirsearch_preflight_injected"] = {
        "java_hits": java_hits,
        "struts_hits": struts_hits,
        "spring_hits": spring_hits,
    }

