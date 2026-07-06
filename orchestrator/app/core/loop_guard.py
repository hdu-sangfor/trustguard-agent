from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


# canonical_params 与 artifact_hash 中需要剔除的"易变但不影响语义"字段。
# 这些字段随每次调用变化（request_id、duration、timestamps、全局 artifact_ref 等），
# 保留它们会导致 loop_signature 永不相同 —— 即便工具产生完全一致的漏洞结论。
# 已在 task-22042fc6... 观察到 9 次完全相同的 nuclei 扫描（severity_histogram 一致、
# vulns 一致）因为 _artifact_ref/duration_ms 不同而从未触发 loop_break。
_VOLATILE_PARAM_KEYS: frozenset[str] = frozenset({
    "request_id",
    "_request_id",
    "_timestamp",
    "timestamp",
    "_plan_id",
    # run_id 故意不在此白名单：不同 run_id 可能代表不同业务意图
})

# artifact_hash 只保留"语义"字段；其余全部丢弃以提高 loop 灵敏度。
# 白名单原则：包含那些在相同工具重复对相同目标执行时应当保持稳定的结构化结论。
_SEMANTIC_ARTIFACT_KEYS: frozenset[str] = frozenset({
    # 通用
    "status",
    "error",
    # web/nuclei 高阶语义
    "vulnerabilities",          # 漏洞命中列表（模板 ID + URL 组合是稳定的）
    "severity_histogram",       # 严重级别计数
    "high_value_facts",         # 高价值事实文本（orch 侧处理后保留）
    "key_findings",             # 精筛的关键命中
    # 网络扫描
    "open_ports",
    "services",
    "tech_stack",
    # 爬虫
    "url_counts",
    "clustered_targets_count",
    # dirsearch
    "matches",
})

# 某些高熵字段即便进入白名单也会扰动 hash，额外从 vulnerabilities 里丢弃这些 per-item 键。
_VOLATILE_VULN_ITEM_KEYS: frozenset[str] = frozenset({
    "evidence",        # 可能含 URL 随机顺序
    "matched_at",      # 时常变化
    "extracted-results",
})


def _strip_params(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (params or {}).items() if k not in _VOLATILE_PARAM_KEYS}


def _project_vuln_items(items: Iterable[Any]) -> list[dict[str, Any]]:
    """对 vulnerabilities 列表做稳定投影：仅保留能唯一定位漏洞的关键字段。"""
    out: list[dict[str, Any]] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        projected = {
            k: it.get(k)
            for k in ("template_id", "template", "url", "severity", "cve")
            if it.get(k) is not None and k not in _VOLATILE_VULN_ITEM_KEYS
        }
        out.append(projected)
    # 排序使 hash 与顺序无关
    try:
        out.sort(key=lambda d: json.dumps(d, ensure_ascii=True, sort_keys=True))
    except Exception:
        pass
    return out


def _project_semantic_artifact(artifacts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(artifacts, dict):
        return {}
    projection: dict[str, Any] = {}
    for k in _SEMANTIC_ARTIFACT_KEYS:
        if k not in artifacts:
            continue
        v = artifacts[k]
        if k == "vulnerabilities" and isinstance(v, list):
            projection[k] = _project_vuln_items(v)
        elif k == "matches" and isinstance(v, list):
            # dirsearch matches：只保留 url+status
            projection[k] = sorted(
                [
                    {kk: m.get(kk) for kk in ("url", "status") if m.get(kk) is not None}
                    for m in v
                    if isinstance(m, dict)
                ],
                key=lambda d: json.dumps(d, ensure_ascii=True, sort_keys=True),
            )
        else:
            projection[k] = v
    return projection


def canonical_params(params: dict[str, Any]) -> str:
    try:
        return json.dumps(_strip_params(params), ensure_ascii=True, sort_keys=True)
    except Exception:
        return str(params)


def artifact_hash(artifacts: dict[str, Any] | None) -> str:
    """
    对 resolved_artifacts 计算稳定语义哈希：只纳入结构化结论字段，
    剔除 _artifact_ref / duration_ms / raw_preview / timestamps 等 per-call 随机波动。
    """
    projection = _project_semantic_artifact(artifacts or {})
    try:
        raw = json.dumps(projection, ensure_ascii=True, sort_keys=True)
    except Exception:
        raw = str(projection)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def update_loop_guard(state: Any, signature: str) -> bool:
    recent = state.recent_exec_signatures
    last = recent[-1] if len(recent) > 0 else None
    if signature == last:
        state.loop_repeat_count += 1
    else:
        state.loop_repeat_count = 0
    recent.append(signature)
    # 触发条件：
    # 1) 连续三次同签名（兼容旧逻辑）
    # 2) 最近 6 次窗口内同签名出现 >= 3（捕捉 A-B-A / A-B-A-B 类振荡循环）
    if state.loop_repeat_count >= 2:
        return True
    window = list(recent)[-6:]
    if window.count(signature) >= 3:
        return True
    return False
