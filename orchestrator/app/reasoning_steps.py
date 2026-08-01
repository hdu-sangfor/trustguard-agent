"""
结构化 CoT 推理步骤类型注册表（Issue #136 切片 A）。

扩展新类型时只改本模块 + 写入校验集合；DB 为字符串列，非 MySQL ENUM。
"""

from __future__ import annotations

from typing import Final

# 合法 step_type 码 → 中文展示名
STEP_TYPE_LABELS_ZH: Final[dict[str, str]] = {
    "TASK_UNDERSTANDING": "任务理解",
    "TASK_PLANNING": "任务规划",
    "RAG_RETRIEVAL": "RAG 检索",
    "TOOL_CALL": "工具调用",
    "RESULT_OBSERVATION": "结果观察",
    "EVIDENCE_JUDGMENT": "证据判断",
    "REPLANNING": "重新规划",
    "FINAL_CONCLUSION": "最终结论",
}

STEP_TYPES: Final[frozenset[str]] = frozenset(STEP_TYPE_LABELS_ZH)

# 切片 A 编排器最小挂钩会实际写出的类型
SLICE_A_EMITTED_STEP_TYPES: Final[frozenset[str]] = frozenset(
    {
        "TASK_PLANNING",
        "TOOL_CALL",
        "RAG_RETRIEVAL",
        "RESULT_OBSERVATION",
        "FINAL_CONCLUSION",
    }
)

STEP_STATUSES: Final[frozenset[str]] = frozenset(
    {"RUNNING", "SUCCEEDED", "FAILED", "SKIPPED"}
)


def label_zh(step_type: str) -> str:
    return STEP_TYPE_LABELS_ZH.get(step_type, step_type)


def is_valid_step_type(step_type: str) -> bool:
    return step_type in STEP_TYPES


def is_valid_status(status: str) -> bool:
    return status in STEP_STATUSES
