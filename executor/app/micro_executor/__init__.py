"""MicroExecutor SDK：V1 Native Skill（run.py）侧防线与旁路嗅探协议的包入口。"""

from .protocol import (
    ARTIFACT_NOTICE_PREFIX,
    PROTOCOL_VERSION,
    build_artifact_notice,
    parse_artifact_notice_line,
)
from .outputs import (
    AGENT_SUMMARY_SCHEMA_VERSION,
    MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV,
    AgentSummaryJSON,
    serialize_for_agent_stdout,
)
from .target_scope import (
    TargetScopeError,
    TargetScopeValidator,
    host_of,
    normalize_scope_host,
)

__all__ = [
    "AGENT_SUMMARY_SCHEMA_VERSION",
    "MICROEXECUTOR_AGENT_SUMMARY_MAX_CHARS_ENV",
    "ARTIFACT_NOTICE_PREFIX",
    "PROTOCOL_VERSION",
    "AgentSummaryJSON",
    "TargetScopeError",
    "TargetScopeValidator",
    "build_artifact_notice",
    "host_of",
    "normalize_scope_host",
    "parse_artifact_notice_line",
    "serialize_for_agent_stdout",
]
