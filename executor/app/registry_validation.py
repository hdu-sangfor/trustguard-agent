"""
docker/tools_registry.yaml 契约校验（R5b / R5c）。

与 `tools_registry.get_tool_info` / `list_skills_with_category` 使用的字段对齐；
单测门禁：`tests/contracts/test_tools_registry_contract_unit.py`。

R5c：可选 `extract_kb_features`（bool）；缺省视为 false（未声明 = no-op / 禁止特征写向量路径）。
"""
from __future__ import annotations

import re
from typing import Any

ALLOWED_RUNNERS = frozenset({"python", "binary"})

# 与 skills 目录命名及现有 YAML 键一致（含 read_workspace_artifact、curl-raw）
_SKILL_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def registry_validation_errors(data: Any) -> list[str]:
    """
    校验解析后的注册表根对象（yaml.safe_load 结果）。
    返回人类可读错误列表；空列表表示通过。
    """
    errs: list[str] = []
    if data is None:
        return ["registry root is null"]
    if not isinstance(data, dict):
        return [f"registry root must be mapping, got {type(data).__name__}"]
    tools = data.get("tools")
    if tools is None:
        errs.append("missing top-level key 'tools'")
        return errs
    if not isinstance(tools, dict):
        errs.append(f"'tools' must be a mapping, got {type(tools).__name__}")
        return errs

    for skill_id, ent in tools.items():
        sid = str(skill_id).strip()
        prefix = f"tools[{sid!r}]"
        if not sid:
            errs.append(f"{prefix}: empty skill_id key")
            continue
        if not _SKILL_ID_RE.match(sid):
            errs.append(f"{prefix}: skill_id must match {_SKILL_ID_RE.pattern}")
        if not isinstance(ent, dict):
            errs.append(f"{prefix}: entry must be a mapping, got {type(ent).__name__}")
            continue

        if "category" not in ent:
            errs.append(f"{prefix}: missing required key 'category'")
        elif not str(ent.get("category") or "").strip():
            errs.append(f"{prefix}: 'category' must be non-empty string")

        if "runner" not in ent:
            errs.append(f"{prefix}: missing required key 'runner'")
        else:
            r = str(ent.get("runner") or "").strip().lower()
            if r not in ALLOWED_RUNNERS:
                errs.append(
                    f"{prefix}: 'runner' must be one of {sorted(ALLOWED_RUNNERS)}, got {ent.get('runner')!r}"
                )

        if "path" not in ent:
            errs.append(f"{prefix}: missing required key 'path' (use empty string for PATH-based tools)")
        else:
            pv = ent["path"]
            if pv is not None and not isinstance(pv, str):
                errs.append(f"{prefix}: 'path' must be string or null, got {type(pv).__name__}")

        if "executable" not in ent:
            errs.append(f"{prefix}: missing required key 'executable' (use empty string when unused)")
        else:
            ev = ent["executable"]
            if ev is not None and not isinstance(ev, str):
                errs.append(f"{prefix}: 'executable' must be string or null, got {type(ev).__name__}")

        if "default_args" in ent and ent["default_args"] is not None:
            da = ent["default_args"]
            if not isinstance(da, list):
                errs.append(f"{prefix}: 'default_args' must be a list, got {type(da).__name__}")
            else:
                for i, item in enumerate(da):
                    if not isinstance(item, (str, int, float, bool)):
                        errs.append(
                            f"{prefix}: 'default_args[{i}]' must be scalar (str/int/float/bool), "
                            f"got {type(item).__name__}"
                        )

        if "extract_kb_features" in ent and ent["extract_kb_features"] is not None:
            v = ent["extract_kb_features"]
            if not isinstance(v, bool):
                errs.append(
                    f"{prefix}: 'extract_kb_features' must be boolean if present (true=实现特征抽取, "
                    f"false/省略=默认禁止向量特征写入路径), got {type(v).__name__}"
                )

        if "internal_agent_plane" in ent and ent["internal_agent_plane"] is not None:
            v = ent["internal_agent_plane"]
            if not isinstance(v, bool):
                errs.append(
                    f"{prefix}: 'internal_agent_plane' must be boolean if present, got {type(v).__name__}"
                )

    return errs
