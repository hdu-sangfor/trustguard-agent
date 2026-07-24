"""告警研判任务请求/响应 Schema。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .models import AlertTriageResult


class CreateTriageTaskRequest(BaseModel):
    """创建告警研判任务请求。

    Scenario ID 仅供测试任务标记，不能用于读取 ground truth。
    """

    alert_uuid: str = Field(..., min_length=1, description="XDR 告警 UUID")
    scenario_id: Optional[str] = Field(
        default=None, description="可选 Scenario ID，仅供测试记录"
    )
    enable_rag: bool = Field(default=True, description="是否启用 RAG 知识增强")
    strategy_params: Optional[dict[str, Any]] = Field(
        default=None, description="可选的研判策略参数"
    )
    caller_notes: Optional[str] = Field(
        default=None, description="调用方备注或上下文"
    )


class TriageTaskStatusResponse(BaseModel):
    """研判任务状态响应。"""

    task_id: str = Field(..., description="任务 ID")
    alert_uuid: str = Field(..., description="XDR 告警 UUID")
    status: str = Field(..., description="任务状态：PENDING / RUNNING / DONE / FAILED")
    current_node: Optional[str] = Field(
        default=None, description="当前工作流节点名称"
    )
    created_at: str = Field(default="", description="创建时间 (ISO 8601)")
    updated_at: str = Field(default="", description="更新时间 (ISO 8601)")


class TriageTaskResultResponse(BaseModel):
    """研判任务结果响应。"""

    task_id: str = Field(..., description="任务 ID")
    alert_uuid: str = Field(..., description="XDR 告警 UUID")
    status: str = Field(..., description="任务状态")
    result: Optional[AlertTriageResult] = Field(
        default=None, description="研判结果；任务未完成时为空"
    )
    error: Optional[str] = Field(default=None, description="失败时的错误信息")


class TriageTaskEventsResponse(BaseModel):
    """研判过程事件响应。"""

    task_id: str = Field(..., description="任务 ID")
    events: list[dict[str, Any]] = Field(
        default_factory=list, description="Trace 事件列表"
    )
