from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.domain.models import ConversationMessage
from app.stores.conversation_store import InMemoryConversationStore, MirroredConversationStore
from app.workflows.pentest import intent


def test_memory_conversation_store_is_actor_scoped_and_idempotent():
    store = InMemoryConversationStore()
    message = ConversationMessage(id="terminal-1", role="assistant", text="done")

    store.append_message("conv-1", "actor-1", message)
    store.append_message("conv-1", "actor-1", message)

    assert [item.id for item in store.get("conv-1", "actor-1").messages] == ["terminal-1"]
    assert store.get("conv-1", "actor-2") is None


def test_memory_conversation_store_lists_recent_actor_conversations():
    store = InMemoryConversationStore()
    store.append_message(
        "conv-1",
        "actor-1",
        ConversationMessage(id="user-1", role="user", text="扫描第一个目标"),
    )
    store.append_message(
        "conv-2",
        "actor-1",
        ConversationMessage(id="user-2", role="user", text="扫描第二个目标"),
    )
    store.append_message(
        "conv-2",
        "actor-1",
        ConversationMessage(
            id="task-2",
            role="assistant",
            text="任务正在执行",
            task_id="task-2",
            task_status="RUNNING",
        ),
    )
    store.append_message(
        "conv-hidden",
        "actor-2",
        ConversationMessage(id="hidden", role="user", text="其他用户会话"),
    )

    summaries = store.list("actor-1")

    assert [item.conversation_id for item in summaries] == ["conv-2", "conv-1"]
    assert summaries[0].title == "扫描第二个目标"
    assert summaries[0].preview == "任务正在执行"
    assert summaries[0].task_id == "task-2"
    assert summaries[0].task_status == "RUNNING"
    assert summaries[0].message_count == 2


def test_mirrored_store_migrates_legacy_cache_and_keeps_writes_in_both_layers():
    durable = InMemoryConversationStore()
    cache = InMemoryConversationStore()
    cache.append_message(
        "conv-legacy",
        "actor-1",
        ConversationMessage(id="user-1", role="user", text="legacy"),
    )
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]

    assert [item.conversation_id for item in store.list("actor-1")] == ["conv-legacy"]
    restored = store.get("conv-legacy", "actor-1")
    restored_texts = [item.text for item in restored.messages]
    store.append_message(
        "conv-legacy",
        "actor-1",
        ConversationMessage(id="assistant-1", role="assistant", text="reply"),
    )

    assert restored_texts == ["legacy"]
    assert [item.text for item in durable.get("conv-legacy", "actor-1").messages] == ["legacy", "reply"]
    assert [item.text for item in cache.get("conv-legacy", "actor-1").messages] == ["legacy", "reply"]


def test_draft_messages_can_be_restored_and_are_actor_isolated(monkeypatch):
    store = InMemoryConversationStore()
    monkeypatch.setattr(main, "_conversations", store)
    monkeypatch.setattr(intent, "extract_pentest_draft", lambda _message: None)
    client = TestClient(main.app)

    draft = client.post(
        "/v1/task-agent/draft",
        headers={"X-Actor-Id": "actor-1"},
        json={"message": "对 https://test.example.com 做非破坏性渗透，运行20分钟"},
    )
    conversation_id = draft.json()["conversationId"]
    restored = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"X-Actor-Id": "actor-1"},
    )
    isolated = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"X-Actor-Id": "actor-2"},
    )

    assert restored.status_code == 200
    assert [message["role"] for message in restored.json()["messages"]] == ["user", "assistant"]
    assert restored.json()["messages"][1]["draft"]["target"] == "https://test.example.com"
    assert isolated.status_code == 404

    listed = client.get("/v1/conversations", headers={"X-Actor-Id": "actor-1"})
    isolated_list = client.get("/v1/conversations", headers={"X-Actor-Id": "actor-2"})
    assert listed.status_code == 200
    assert listed.json()[0]["conversationId"] == conversation_id
    assert listed.json()[0]["title"].startswith("对 https://test.example.com")
    assert listed.json()[0]["messageCount"] == 2
    assert isolated_list.json() == []
