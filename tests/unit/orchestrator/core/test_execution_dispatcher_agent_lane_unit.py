"""execution_dispatcher：MQ 模式统一发布至 **`MQ_TOPIC_AGENT`**。"""
from __future__ import annotations

import pytest

from tests.orchestrator_test_env import orchestrator_sys_path_isolated


def test_mq_publish_topic_always_uses_mq_topic_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import mq_publish_topic_for_skill

        monkeypatch.setenv("MQ_TOPIC_AGENT", "execute_tasks_agent")
        assert mq_publish_topic_for_skill("nmap") == "execute_tasks_agent"
        assert mq_publish_topic_for_skill("nmap") == "execute_tasks_agent"


def test_mq_publish_topic_respects_custom_agent_queue_name(monkeypatch: pytest.MonkeyPatch) -> None:
    with orchestrator_sys_path_isolated():
        from app.core.execution_dispatcher import mq_publish_topic_for_skill

        monkeypatch.setenv("MQ_TOPIC_AGENT", "my-single-lane")
        assert mq_publish_topic_for_skill("httpx") == "my-single-lane"


def test_publish_mq_execute_task_uses_agent_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    with orchestrator_sys_path_isolated():
        from app.core import execution_dispatcher as ed

        published: dict = {}

        class _Pub:
            def publish(self, topic: str, body: bytes) -> None:
                published["topic"] = topic
                published["body_len"] = len(body)

            def close(self) -> None:
                pass

        monkeypatch.setenv("MQ_BROKER_URL", "amqp://guest:guest@localhost:5672/%2F")
        monkeypatch.setenv("MQ_TOPIC_AGENT", "agent-q")
        monkeypatch.setattr(ed, "_get_mq_publisher", lambda: _Pub())

        from app.schemas.mq_execute_task import MQExecuteTaskMessage

        msg = MQExecuteTaskMessage(
            request_id="r1",
            task_id="t1",
            skill_id="nuclei",
            target="http://a.com",
            allowed_target="http://a.com",
            params={},
            context={},
        )
        ed._publish_mq_execute_task(msg)
        assert published.get("topic") == "agent-q"
        assert int(published.get("body_len") or 0) > 0
