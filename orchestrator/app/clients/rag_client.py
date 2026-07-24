"""RAG Client — 调用 trustguard-rag 检索知识。

当前为占位实现，RAG 不可用时返回降级标记。
后续 Issue 接入真实 RAG 后需实现检索+生成流程。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

RAG_BASE_URL = os.getenv("RAG_BASE_URL", "http://localhost:18980").rstrip("/")
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() in ("true", "1", "yes", "on")


class RAGDegradedError(Exception):
    """RAG 服务不可用时的降级异常。"""

    def __init__(self, reason: str = "RAG service unavailable"):
        super().__init__(reason)
        self.reason = reason


class RAGResponse:
    """RAG 检索回答结构化结果。"""

    def __init__(
        self,
        answer: str = "",
        citations: Optional[list[dict[str, Any]]] = None,
        degraded: bool = False,
    ):
        self.answer = answer
        self.citations = citations or []
        self.degraded = degraded


async def query_rag(
    questions: list[str],
    context: Optional[dict[str, Any]] = None,
) -> RAGResponse:
    """查询 trustguard-rag 获取安全知识。

    当前占位实现：始终返回降级状态。
    后续接入真实 RAG 后需根据告警字段（命令行、进程、文件、IP、CVE、ATT&CK）
    生成检索问题并解析 citation。

    Args:
        questions: 检索问题列表
        context: 可选的上下文信息（告警字段等）

    Returns:
        RAGResponse: 结构化回答，degraded=True 时表示服务不可用
    """
    if not RAG_ENABLED:
        return RAGResponse(degraded=True)

    # TODO: 接入真实 trustguard-rag 服务
    # 1. POST {RAG_BASE_URL}/api/v1/rag/query
    # 2. 解析 answer + citations
    # 3. 失败时返回 degraded=True
    logger.warning(
        "RAG query is a placeholder (RAG_ENABLED=%s, RAG_BASE_URL=%s); "
        "returning degraded response. "
        "Questions: %s",
        RAG_ENABLED,
        RAG_BASE_URL,
        questions,
    )
    return RAGResponse(degraded=True)


async def health_check() -> bool:
    """检查 RAG 服务是否可用。"""
    if not RAG_ENABLED:
        return False
    # TODO: 实际健康检查
    return False


def build_questions_from_alert(alert: dict[str, Any]) -> list[str]:
    """根据告警字段生成 RAG 检索问题。

    从告警的 alert_type、命令行、进程、文件、IP、CVE、ATT&CK 等信息
    构建一个或多个检索问题。

    Args:
        alert: XDR 告警数据（dict）

    Returns:
        检索问题字符串列表
    """
    questions: list[str] = []

    alert_type = str(alert.get("alert_type") or alert.get("type") or "").strip()
    if alert_type:
        questions.append(f"什么是 {alert_type} 类型的告警？有哪些常见误报场景？")

    # 提取命令行/进程信息
    cmd = str(alert.get("command_line") or alert.get("cmd") or "").strip()
    process = str(
        alert.get("process_name")
        or alert.get("process")
        or alert.get("image_name")
        or ""
    ).strip()
    if cmd or process:
        parts = [p for p in [process, cmd] if p]
        questions.append(f"{'; '.join(parts)} 是否属于正常业务行为？")

    # 文件路径
    file_path = str(alert.get("file_path") or alert.get("path") or "").strip()
    if file_path:
        questions.append(f"文件 {file_path} 是否可能是恶意文件？")

    # CVE
    cve = str(alert.get("cve") or "").strip().upper()
    if cve and cve.startswith("CVE-"):
        questions.append(f"漏洞 {cve} 的利用方式和影响范围是什么？")

    # ATT&CK
    attck = str(alert.get("attack_technique") or alert.get("attack_id") or "").strip()
    if attck:
        questions.append(f"ATT&CK 技术 {attck} 的常见检测方法和误报场景？")

    # IP 信誉
    ip = str(alert.get("source_ip") or alert.get("dst_ip") or "").strip()
    if ip:
        questions.append(f"IP {ip} 的信誉如何？是否已知恶意？")

    if not questions:
        questions.append("该告警的常见研判方法和证据需求是什么？")

    return questions
