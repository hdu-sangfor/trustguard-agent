"""
框架识别共享模块（Fix D5）。

背景：THREAT_MODEL 阶段的硬规则 `_normalize_framework_from_context` +
`_infer_framework_from_state_fallback` 仅在 THREAT_MODEL 阶段触发；
当任务从 RECON 直接跳到 VULN_SCAN（iml>=2 备选通道，Fix D2）时，
phase_transition_guard.normalize_framework_unknown_if_needed 只会把 framework_target
设为 GENERIC_WEB / CUSTOM_APP 等通用 marker，永远不会识别出 struts2 / spring / thinkphp。

实地观测（r3~r7，6/12 次出现此漂移）：
- r3 thinkphp: framework_target=CUSTOM_APP
- r4 struts2:  framework_target=GENERIC_WEB
- r6 thinkphp: framework_target=GENERIC_WEB
- r7 thinkphp: framework_target=CUSTOM_APP

系统通过 nuclei 技能侧 fallback（模板路径找不到 /skill/temlists/<marker>
时用根目录 /skill/temlists 全量扫）自救成功，但 orchestrator 侧的 prompt
拿不到准确的 framework_target，影响下一轮 LLM 决策质量。

本模块把两个 detector 从 state_machine 提取出来，允许 phase_transition_guard
也能在不产生循环依赖的前提下复用。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "detect_framework_from_context",
    "detect_framework_from_state_fallback",
]


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    if isinstance(v, str):
        return v.strip().lower() not in ("", "0", "false", "none", "null")
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) > 0
    return bool(v)


def _append_any(v: Any, parts: list[str]) -> None:
    """把多种结构类型里可用的字符串碎片展平进 parts。"""
    if isinstance(v, str) and v.strip():
        parts.append(v.strip())
    elif isinstance(v, dict):
        for k in ("name", "signal", "template_id", "url", "message", "text", "tech"):
            x = v.get(k)
            if x:
                parts.append(str(x))
    elif isinstance(v, list):
        for x in v[:200]:
            _append_any(x, parts)


def detect_framework_from_context(ctx: dict[str, Any]) -> tuple[str, list[str]]:
    """
    硬规则：从 ctx 中的技术证据和高价值端点归一框架标签。
    返回 (framework, evidence_hits)；未命中返回 ("", [])。

    注意：struts2 关键词检测仅从强信号来源（tech_stack_evidence, suspicious_signals
    等）触发，不从 URL 路径（high_value_endpoints）触发——防止 dirsearch 词表里的
    struts2-showcase 路径（实际 404）误判为 struts2 目标。
    """
    if not isinstance(ctx, dict):
        return "", []
    ap = ctx.get("asset_path_profile")
    if isinstance(ap, dict):
        hint = str(ap.get("stack_hint") or "").strip()
        if hint == "struts2_heavy":
            return "struts2", ["asset_path_profile:struts2_heavy"]
        if hint == "php_heavy":
            return "php", ["asset_path_profile:php_heavy"]

    # "强信号"：技术栈指纹 / 可疑信号 / 框架提示字段
    # tech_stack: httpx skill's raw tech array (may include "weblogic" from secondary probe)
    _STRONG_KEYS = (
        "tech_stack_evidence",
        "pipeline_tech_stack_evidence",
        "suspicious_signals",
        "framework_hint",
        "framework_target",
        "tech_stack",
        "title",
        "http-enum_title",
        "raw_preview",
        "http-enum_raw_preview",
        "curl-raw_raw_preview",
    )
    # "弱信号"：URL/路径列表（词表扫描结果可能包含不存在的路径）
    _URL_KEYS = (
        "dispatcher_high_value_endpoints",
        "high_value_endpoints",
        "target",
    )

    strong_parts: list[str] = []
    url_parts: list[str] = []
    evidence: list[str] = []
    for key in _STRONG_KEYS:
        _append_any(ctx.get(key), strong_parts)
    for key in _URL_KEYS:
        _append_any(ctx.get(key), url_parts)

    strong_blob = "\n".join(strong_parts).lower()
    url_blob = "\n".join(url_parts).lower()
    blob = strong_blob + "\n" + url_blob

    if not blob.strip():
        return "", []

    # --- 强信号框架匹配（任何关键词均可触发）---
    if any(k in strong_blob for k in ("struts2", "apache struts", "struts")):
        evidence.append("keyword:struts-strong")
        return "struts2", evidence
    if any(k in strong_blob for k in ("spring boot", "springboot", "spring framework", "actuator")):
        evidence.append("keyword:spring-strong")
        return "spring", evidence
    if "thinkphp" in strong_blob:
        evidence.append("keyword:thinkphp-strong")
        return "thinkphp", evidence
    if any(k in strong_blob for k in ("jenkins", "jenkins ci", "jenkins:2.", "jenkins:1.")):
        evidence.append("keyword:jenkins-strong")
        return "jenkins", evidence
    if any(k in strong_blob for k in ("elasticsearch", "elastic search", "kibana")):
        evidence.append("keyword:elasticsearch-strong")
        return "elasticsearch", evidence
    if any(k in strong_blob for k in ("apache solr", "solr admin", "solr:8.", "solr:7.", "solr:6.")):
        evidence.append("keyword:solr-strong")
        return "solr", evidence
    if any(k in strong_blob for k in ("weblogic", "oracle weblogic", "weblogic server")):
        evidence.append("keyword:weblogic-strong")
        return "weblogic", evidence
    if any(k in strong_blob for k in ("shiro", "apache shiro", "remembermecookie", "remembreme=deleteme")):
        evidence.append("keyword:shiro-strong")
        return "shiro", evidence
    if any(k in strong_blob for k in ("nacos", "alibaba nacos", "nacos-server")):
        evidence.append("keyword:nacos-strong")
        return "nacos", evidence
    if any(k in strong_blob for k in ("xxl-job", "xxl job", "xxljob", "xxl_job")):
        evidence.append("keyword:xxljob-strong")
        return "xxl-job", evidence
    if any(k in strong_blob for k in ("hadoop", "yarn resourcemanager", "resourcemanagerversion", "clusterinfo")):
        evidence.append("keyword:hadoop-strong")
        return "hadoop", evidence

    # --- URL 路径信号（仅极高置信度特征触发）---
    # .action 后缀是 Struts2 的强 URL 特征，几乎不误报
    if ".action" in url_blob:
        evidence.append("url-suffix:.action")
        return "struts2", evidence
    # /console/login/ 是 WebLogic 管理控制台专属路径
    if "/console/login/" in url_blob or "adminconsolesession" in url_blob:
        evidence.append("url-path:weblogic-console")
        return "weblogic", evidence
    # 全局 blob（强+弱）匹配其他框架关键词（URL 中明确出现框架名仍算有效）
    if any(k in blob for k in ("spring boot", "springboot", "spring framework", "actuator")):
        evidence.append("keyword:spring")
        return "spring", evidence
    if "thinkphp" in blob:
        evidence.append("keyword:thinkphp")
        return "thinkphp", evidence
    if any(k in blob for k in ("jenkins", "jenkins ci", "jenkins:2.", "jenkins:1.")):
        evidence.append("keyword:jenkins")
        return "jenkins", evidence
    if any(k in blob for k in ("elasticsearch", "elastic search", "kibana")):
        evidence.append("keyword:elasticsearch")
        return "elasticsearch", evidence
    if any(k in blob for k in ("apache solr", "solr admin", "solr:8.", "solr:7.", "solr:6.")):
        evidence.append("keyword:solr")
        return "solr", evidence
    if any(k in blob for k in ("weblogic", "oracle weblogic", "weblogic server")):
        evidence.append("keyword:weblogic")
        return "weblogic", evidence
    if any(k in blob for k in ("shiro", "apache shiro", "remembermecookie", "remembreme=deleteme")):
        evidence.append("keyword:shiro")
        return "shiro", evidence
    if any(k in blob for k in ("nacos", "alibaba nacos", "nacos-server")):
        evidence.append("keyword:nacos")
        return "nacos", evidence
    if any(k in blob for k in ("xxl-job", "xxl job", "xxljob", "xxl_job")):
        evidence.append("keyword:xxljob")
        return "xxl-job", evidence
    if any(k in blob for k in ("hadoop", "yarn resourcemanager", "resourcemanagerversion", "clusterinfo", "ws/v1/cluster")):
        evidence.append("keyword:hadoop")
        return "hadoop", evidence
    return "", evidence


def detect_framework_from_state_fallback(state: Any) -> tuple[str, list[str]]:
    """
    兜底推断：当黑板未明确 framework_target 时，从 confirmed_facts /
    history_summary / target_context 的自然语言证据补推断。
    """
    evidence: list[str] = []
    parts: list[str] = []
    # Separate "authoritative" fields from bulk text to avoid struts
    # false-positives caused by nuclei template IDs / LLM commentary
    # mentioning "struts2" on non-struts targets.
    authoritative_parts: list[str] = []
    ctx = getattr(state, "target_context", None)
    if isinstance(ctx, dict):
        for key in ("framework_target", "framework_hint"):
            v = ctx.get(key)
            if isinstance(v, str) and v.strip():
                authoritative_parts.append(v.strip())
        for key in ("target", "tech_stack_evidence", "suspicious_signals"):
            v = ctx.get(key)
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v[:120])
            elif isinstance(v, dict):
                parts.extend(str(x) for x in v.values())
    facts = getattr(state, "confirmed_facts", None) or []
    facts_blob = "\n".join(str(x) for x in facts[:200]).lower()
    parts.extend(str(x) for x in facts[:200])
    hs = str(getattr(state, "history_summary", "") or "")
    if hs:
        parts.append(hs)
    blob = "\n".join(parts).lower()
    auth_blob = "\n".join(authoritative_parts).lower()
    if not blob.strip() and not auth_blob.strip():
        return "", evidence
    # Struts: match from authoritative fields OR confirmed_facts (high-confidence).
    # Avoid matching from history_summary/tech_stack_evidence which contain
    # nuclei template names like "Struts2 S2-045 Fingerprint".
    if "struts2" in auth_blob or "struts" in auth_blob or "struts2" in facts_blob:
        evidence.append("fallback:struts-authoritative" if ("struts2" in auth_blob or "struts" in auth_blob) else "fallback:struts-confirmed_facts")
        return "struts2", evidence
    if "spring boot" in blob or "springboot" in blob or "spring framework" in blob or "actuator" in blob:
        evidence.append("fallback:spring")
        return "spring", evidence
    if "thinkphp" in blob:
        evidence.append("fallback:thinkphp")
        return "thinkphp", evidence
    if any(k in blob for k in ("jenkins", "jenkins ci", "jenkins:2.", "jenkins:1.")):
        evidence.append("fallback:jenkins")
        return "jenkins", evidence
    if any(k in blob for k in ("elasticsearch", "elastic search", "kibana")):
        evidence.append("fallback:elasticsearch")
        return "elasticsearch", evidence
    if any(k in blob for k in ("apache solr", "solr admin", "solr:")):
        evidence.append("fallback:solr")
        return "solr", evidence
    if any(k in blob for k in ("weblogic", "oracle weblogic", "weblogic server")):
        evidence.append("fallback:weblogic")
        return "weblogic", evidence
    if any(k in blob for k in ("nacos", "alibaba nacos", "nacos-server")):
        evidence.append("fallback:nacos")
        return "nacos", evidence
    if any(k in blob for k in ("xxl-job", "xxl job", "xxljob", "xxl_job")):
        evidence.append("fallback:xxljob")
        return "xxl-job", evidence
    return "", evidence
