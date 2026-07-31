"""Shared, protocol-independent knowledge access for Agent workflows."""

from app.knowledge.gateway import KnowledgeGateway
from app.knowledge.models import KnowledgeSearchRequest, KnowledgeSearchResponse

__all__ = ["KnowledgeGateway", "KnowledgeSearchRequest", "KnowledgeSearchResponse"]
