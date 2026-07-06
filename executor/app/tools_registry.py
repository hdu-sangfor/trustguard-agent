"""
从 docker/tools_registry.yaml 解析每个工具的安装路径，供 Executor 调用真实工具时使用。

环境变量：
- TRUSTGUARD_TOOLS_ROOT：工具根目录，未设置时默认使用项目根下的 TRUSTGUARD_TOOLS_ROOT
- TOOLS_REGISTRY_YAML：注册表 YAML 路径，未设置时默认使用项目根下的 docker/tools_registry.yaml

R5b：`tools` 下每条目的必填键与 `registry_validation.registry_validation_errors` 对齐
（category、runner、path、executable；path/executable 可为空字符串；可选 default_args）。
R5c：可选 `extract_kb_features`（bool）；缺省 false。见 `kb_feature_policy.extract_kb_features_declared`。
CI 门禁：`tests/contracts/test_tools_registry_contract_unit.py`。
"""
from pathlib import Path
from typing import Any, Optional

# 项目根 = 2026服务外包（executor 的上级的上级）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TOOLS_ROOT = _PROJECT_ROOT / "TRUSTGUARD_TOOLS_ROOT"
_DEFAULT_REGISTRY = _PROJECT_ROOT / "docker" / "tools_registry.yaml"


def _load_registry() -> dict:
    import os
    import re

    reg_path = os.environ.get("TOOLS_REGISTRY_YAML") or str(_DEFAULT_REGISTRY)
    path = Path(reg_path)
    if not path.exists():
        return {"tools": {}}
    raw = path.read_text(encoding="utf-8")
    # 简单替换 ${TRUSTGUARD_TOOLS_ROOT}
    tools_root = os.environ.get("TRUSTGUARD_TOOLS_ROOT") or str(_DEFAULT_TOOLS_ROOT)
    raw = re.sub(r"\$\{TRUSTGUARD_TOOLS_ROOT\}", tools_root.replace("\\", "/"), raw)
    try:
        import yaml
        return yaml.safe_load(raw) or {}
    except Exception:
        return {"tools": {}}


def get_tools_root() -> Path:
    import os
    root = os.environ.get("TRUSTGUARD_TOOLS_ROOT")
    if root:
        return Path(root)
    return _DEFAULT_TOOLS_ROOT


def get_tool_info(skill_id: str) -> Optional[dict[str, Any]]:
    """
    返回 skill_id 在注册表中的配置；若不存在则返回 None。
    返回结构：{"path": Path, "executable": str, "runner": "python"|"binary", "category": str, ...}
    """
    data = _load_registry()
    tools = data.get("tools") or {}
    entry = tools.get(skill_id)
    if not entry or not isinstance(entry, dict):
        return None
    root = get_tools_root()
    rel = (entry.get("path") or "").strip().replace("\\", "/")
    if not rel:
        # 表示使用系统 PATH
        return {
            "path": None,
            "full_path": None,
            "executable": entry.get("executable") or skill_id,
            "runner": (entry.get("runner") or "binary").lower(),
            "category": entry.get("category") or "",
            "default_args": entry.get("default_args") or [],
            "extract_kb_features": entry.get("extract_kb_features") is True,
            "internal_agent_plane": entry.get("internal_agent_plane") is True,
        }
    full = (root / rel).resolve()
    exe = (entry.get("executable") or "").strip() or skill_id
    return {
        "path": full,
        "full_path": full / exe if exe else full,
        "executable": exe,
        "runner": (entry.get("runner") or "binary").lower(),
        "category": entry.get("category") or "",
        "default_args": entry.get("default_args") or [],
        "extract_kb_features": entry.get("extract_kb_features") is True,
        "internal_agent_plane": entry.get("internal_agent_plane") is True,
    }


def list_registered_skills() -> list[str]:
    """返回已注册的 skill_id 列表。"""
    data = _load_registry()
    tools = data.get("tools") or {}
    return list(tools.keys())


def registry_contract_errors() -> list[str]:
    """返回当前注册表相对 R5b 契约的违例说明；空列表表示通过（供诊断/运维脚本）。"""
    from app.registry_validation import registry_validation_errors

    return registry_validation_errors(_load_registry())


def _normalize_category_token(token: str) -> str:
    """
    将 category 片段规范为可比较形式，避免 YAML 与 PHASE 映射因大小写/下划线不一致导致漏匹配。
    例：VulnScan、vuln_scan → vulnscan；Exploit、exploit → exploit
    """
    t = (token or "").strip().lower()
    return t.replace("_", "")


# 编排器阶段与 tools_registry 中 category 的对应（用于 GET /v1/skills?phase=）
# 与 YAML 里 category 按 "/" 切分后的片段做交集；比较时大小写不敏感且忽略下划线（见 _normalize_category_token）
#
# THREAT_MODEL 收窄说明：
# 该阶段应当是"基于已有证据做威胁建模与假设列举"，不应重复运行 recon 类枚举工具。
# 已在 task-22042fc6... 观察到：read_workspace_artifact 失败后 LLM 退化为
# 在 THREAT_MODEL 重新调用 httpx/http-enum/dirsearch（共 5 次，浪费 ~2 分钟）。
# 因此移除 WebRecon，仅保留 Search（baidu-search 等信息检索）与通过 Search/*/* 纳入的
# read_workspace_artifact / read_target_list（读取已有 artifact 做分析）。
PHASE_TO_CATEGORIES: dict[str, list[str]] = {
    "RECON": ["NetworkScan", "Recon", "Search", "WebRecon"],
    # Search: baidu-search, read_workspace_artifact, read_target_list
    # ThreatModel: curl-raw, dispatcher（已注册 ThreatModel category；定向探测和上下文整合，非枚举）
    "THREAT_MODEL": ["Search", "ThreatModel"],
    "VULN_SCAN": ["VulnScan", "vuln_scan"],
    "EXPLOIT": ["WebTesting", "Exploit"],
    "REPORT": [],
    "DONE": [],
}


def list_skills_with_category() -> list[dict[str, Any]]:
    """返回 [{"skill_id": str, "category": str}, ...]（不含 internal_agent_plane 内部项）。"""
    data = _load_registry()
    tools = data.get("tools") or {}
    return [
        {
            "skill_id": sid,
            "category": (ent.get("category") or "").strip() or "",
            "extract_kb_features": ent.get("extract_kb_features") is True,
        }
        for sid, ent in tools.items()
        if isinstance(ent, dict) and ent.get("internal_agent_plane") is not True
    ]


def get_skill_ids_for_phase(phase: str) -> list[str]:
    """根据编排器阶段返回该阶段可用的 skill_id 列表（仅来自注册表，不校验是否有实现）。"""
    cats = PHASE_TO_CATEGORIES.get(phase.upper() if phase else "", [])
    if not cats:
        return []
    phase_norms = {_normalize_category_token(c) for c in cats if str(c).strip()}
    items = list_skills_with_category()
    out: list[str] = []
    for s in items:
        raw = (s.get("category") or "").strip()
        segs = [x.strip() for x in raw.split("/") if x.strip()]
        seg_norms = {_normalize_category_token(x) for x in segs}
        if not seg_norms.isdisjoint(phase_norms):
            out.append(s["skill_id"])
    return out
