"""
`MQ_AGENT_DAEMON_ARGV_JSON`：Pydantic 强校验（非空 JSON 字符串数组），与 MQ 消息契约一致遵守入参收口。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import Field, RootModel, ValidationError

logger = logging.getLogger(__name__)


class MqAgentDaemonArgvSpec(RootModel):
    """子进程 argv：占位符在 `mq_agent_daemon._expand_argv` 展开。"""

    root: list[str] = Field(min_length=1)


def parse_mq_agent_daemon_argv_json(raw: str | None) -> list[str] | None:
    """
    解析环境变量体；非法 JSON、非数组、空列表或非 str 元素返回 None（调用方回退占位 argv）。
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("MQ_AGENT_DAEMON_ARGV_JSON: invalid JSON")
        return None
    try:
        return MqAgentDaemonArgvSpec.model_validate(data).root
    except ValidationError as e:
        logger.warning("MQ_AGENT_DAEMON_ARGV_JSON: pydantic validation failed: %s", e)
        return None
