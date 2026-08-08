"""Alert-triage adapter for the upstream trustguard-rag MCP v1 contract."""
from __future__ import annotations

from typing import Any

from app.knowledge.config import KnowledgeMcpSettings
from app.knowledge.gateway import get_knowledge_gateway
from app.knowledge.mcp_client import KnowledgeCallContext, McpKnowledgeTransportError
from app.knowledge.models import KnowledgeSearchRequest

_MAX_RESOURCES = 3
_MAX_RESOURCE_CHARS = 1_800
_MAX_CONTEXT_CHARS = 5_000


class RAGResponse:
    def __init__(
        self,
        answer: str = "",
        citations: list[dict[str, Any]] | None = None,
        degraded: bool = False,
        reason: str | None = None,
    ) -> None:
        self.answer = answer
        self.citations = citations or []
        self.degraded = degraded
        self.reason = reason


def _query_text(questions: list[str]) -> str:
    text = "\n".join(f"- {question.strip()}" for question in questions if question.strip())
    return text[:2_000] or "请提供该安全告警的研判证据和处置建议。"


def _validate_resource(resource: Any, hit: Any, scope: str) -> None:
    if resource.resource_ref != hit.resource_ref:
        raise ValueError("MCP_RESOURCE_REF_MISMATCH")
    if resource.scope != scope:
        raise ValueError("MCP_RESOURCE_SCOPE_MISMATCH")
    if resource.source_revision != hit.source_revision:
        raise ValueError("MCP_RESOURCE_REVISION_MISMATCH")
    if resource.content_hash != hit.content_hash:
        raise ValueError("MCP_RESOURCE_HASH_MISMATCH")


async def query_rag(
    questions: list[str],
    context: dict[str, Any] | None = None,
) -> RAGResponse:
    """Retrieve and validate up to three alert-triage knowledge resources.

    The MCP payload is the only RAG protocol used by alert triage.  Any MCP
    fault is deliberately fail-open so XDR-only triage can still complete.
    """
    settings = KnowledgeMcpSettings.from_env()
    if not settings.enabled:
        return RAGResponse(degraded=True, reason="RAG_MCP_DISABLED")

    ctx = context or {}
    task_id = str(ctx.get("task_id") or "alert-triage")
    call_context = KnowledgeCallContext(
        task_id=task_id,
        request_id=f"{task_id}:rag",
        workflow_type="alert-triage",
        workspace_id=settings.workspace_id,
    )
    # Scope is a deployment-owned logical alias.  Alert triage identifies
    # itself through ``workflow_type`` below, but must not bypass the configured
    # MCP scope mapping by hard-coding an alias that may not exist upstream.
    request_scope = settings.scope
    request = KnowledgeSearchRequest(
        query=_query_text(questions),
        scope=request_scope,
        mode=settings.mode,
        limit=min(settings.limit, _MAX_RESOURCES),
    )
    try:
        response = await get_knowledge_gateway(settings).search(request, context=call_context)
        if response.status == "degraded":
            return RAGResponse(degraded=True, reason="RAG_MCP_DEGRADED")
        if not response.hits:
            return RAGResponse(degraded=True, reason="RAG_EMPTY_RESULT")

        citations: list[dict[str, Any]] = []
        passages: list[str] = []
        for hit in response.hits[:_MAX_RESOURCES]:
            resource = await get_knowledge_gateway(settings).read_resource(
                hit.resource_uri, context=call_context
            )
            _validate_resource(resource, hit, request_scope.value)
            snippet = resource.text[:_MAX_RESOURCE_CHARS]
            citations.append(
                {
                    "chunk_id": hit.resource_ref,
                    "content_snippet": snippet[:800],
                    "source": resource.title or resource.filename or hit.title or "trustguard-rag",
                    "resource_uri": hit.resource_uri,
                    "source_uri": resource.source_uri,
                    "page_no": resource.page_no,
                    "score": hit.score,
                    "source_revision": resource.source_revision,
                    "content_hash": resource.content_hash,
                }
            )
            passages.append(f"[RAG:{hit.resource_ref}]\n{snippet}")
        return RAGResponse(answer="\n\n".join(passages)[:_MAX_CONTEXT_CHARS], citations=citations)
    except McpKnowledgeTransportError as exc:
        return RAGResponse(degraded=True, reason=exc.code)
    except Exception as exc:
        return RAGResponse(degraded=True, reason=str(exc)[:120] or "RAG_MCP_UNAVAILABLE")


async def health_check() -> bool:
    """A full MCP contract check occurs on query; this reports feature enablement."""
    return KnowledgeMcpSettings.from_env().enabled


def build_questions_from_alert(alert: dict[str, Any]) -> list[str]:
    """Build bounded queries only from known alert fields."""
    questions: list[str] = []
    alert_name = str(alert.get("name") or alert.get("title") or "").strip()
    alert_type = str(
        alert.get("alert_type")
        or alert.get("type")
        or alert.get("threatType")
        or alert.get("threatClass")
        or ""
    ).strip()
    if alert_name or alert_type:
        topic = " ".join(part[:240] for part in (alert_name, alert_type) if part)
        questions.append(f"{topic} 告警的攻击手法、研判证据、常见误报场景和处置建议")
    description = str(alert.get("description") or "").strip()
    if description:
        questions.append(f"告警描述涉及的检测证据和处置：{description[:400]}")
    searchable = " ".join((alert_name, alert_type, description)).lower()
    entity_hints: list[str] = []
    hint_rules = (
        (("webshell", "web shell", "文件上传", "上传并执行"), "CWE-434 Unrestricted Upload of File with Dangerous Type"),
        (("命令执行", "command execution", "cmd.exe", "shell"), "CWE-78 OS Command Injection"),
        (("powershell", "编码命令"), "PowerShell ATT&CK T1059.001 Command and Scripting Interpreter"),
        (("钓鱼", "phishing"), "Phishing ATT&CK T1566"),
        (("xss", "跨站脚本"), "CWE-79 Cross-site Scripting"),
        (("路径穿越", "path traversal"), "CWE-22 Path Traversal"),
    )
    for needles, hint in hint_rules:
        if any(needle in searchable for needle in needles):
            entity_hints.append(hint)
    if entity_hints:
        questions.append("相关安全知识实体：" + "；".join(entity_hints))
    process = str(alert.get("process_name") or alert.get("process") or alert.get("image_name") or "").strip()
    command = str(alert.get("command_line") or alert.get("cmd") or "").strip()
    if process or command:
        questions.append(f"进程和命令行研判：{process[:200]} {command[:500]}")
    for key, label in (("cve", "CVE"), ("attack_technique", "ATT&CK"), ("attack_id", "ATT&CK")):
        value = str(alert.get(key) or "").strip()
        if value:
            questions.append(f"{label} {value[:160]} 的检测证据和处置建议")
            break
    if not questions:
        questions.append("安全告警的研判方法、证据需求和人工处置建议")
    return questions
