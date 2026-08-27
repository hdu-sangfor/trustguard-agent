from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import json

from fastapi.testclient import TestClient
import pytest

from app import main
from app.domain.models import ConversationMessage
from app.security.confirmation import DraftStore
from app.stores.conversation_store import (
    InMemoryConversationStore,
    MirroredConversationStore,
    MySqlConversationStore,
    RedisConversationStore,
)
from app.workflows.pentest import intent


class _ConversationRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def lrange(self, key, _start, _end):
        return list(self.lists.get(str(key), []))

    def pipeline(self, transaction=True):
        assert transaction is True
        return self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def watch(self, _key):
        return True

    def unwatch(self):
        return True

    def scan_iter(self, match, count=10):
        del count
        return iter(key for key in self.lists if fnmatch.fnmatch(key, match))

    def hgetall(self, key):
        return dict(self.hashes.get(str(key), {}))

    def hset(self, key, mapping):
        self.hashes.setdefault(str(key), {}).update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping)

    def expire(self, _key, _seconds):
        return True

    def delete(self, *keys):
        removed = 0
        for key in map(str, keys):
            removed += int(self.lists.pop(key, None) is not None)
            removed += int(self.hashes.pop(key, None) is not None)
        return removed


class _ConversationMySql:
    def __init__(self):
        self.executions: list[tuple[str, object]] = []
        self.current_sql = ""
        self.current_params: object = None
        self.rowcount = 0
        self.title = "原始标题"
        self.is_pinned = 0
        self.pinned_at: datetime | None = None

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.executions.append((normalized, params))
        self.current_sql = normalized
        self.current_params = params
        self.rowcount = 0
        if not normalized.startswith("UPDATE tg_agent_conversation SET"):
            return
        values = list(params or [])
        actor_id, conversation_id = values[-2:]
        if (actor_id, conversation_id) != ("actor-1", "conv-target"):
            return
        value_index = 0
        if "title = %s" in normalized:
            self.title = str(values[value_index])
            value_index += 1
        if "is_pinned = %s" in normalized:
            self.is_pinned = int(values[value_index])
            value_index += 1
            if "pinned_at = %s" in normalized:
                self.pinned_at = values[value_index]
            else:
                self.pinned_at = datetime(2026, 8, 25, 10, 30) if self.is_pinned else None
        self.rowcount = 1

    def _target_row(self):
        return {
            "conversation_id": "conv-target",
            "task_id": None,
            "title": self.title,
            "created_at": datetime(2026, 8, 25, 8, 0),
            "updated_at": datetime(2026, 8, 25, 9, 0),
            "message_count": 1,
            "preview": "原始标题",
            "task_status": None,
            "is_pinned": self.is_pinned,
            "pinned_at": self.pinned_at,
        }

    @staticmethod
    def _other_row():
        return {
            "conversation_id": "conv-newer",
            "task_id": None,
            "title": "较新会话",
            "created_at": datetime(2026, 8, 25, 8, 30),
            "updated_at": datetime(2026, 8, 25, 10, 0),
            "message_count": 1,
            "preview": "较新会话",
            "task_status": None,
            "is_pinned": 0,
            "pinned_at": None,
        }

    def fetchone(self):
        params = tuple(self.current_params or ())
        if params == ("actor-1", "conv-target"):
            return self._target_row()
        return None

    def fetchall(self):
        if self.current_sql.startswith("SELECT message_id"):
            params = tuple(self.current_params or ())
            if params == ("actor-1", "conv-target"):
                return [
                    {
                        "message_id": "user-1",
                        "role": "user",
                        "message_text": "原始标题",
                        "activities_json": None,
                        "draft_json": None,
                        "confirmation_token": None,
                        "task_id": None,
                        "task_status": None,
                        "created_at": datetime(2026, 8, 25, 8, 0),
                    }
                ]
            return []
        if "c.conversation_id = %s" in self.current_sql:
            row = self.fetchone()
            return [row] if row else []
        if "FROM tg_agent_conversation c" in self.current_sql:
            return [self._target_row(), self._other_row()]
        return []


class _MySqlMigrationError(Exception):
    pass


class _PinMigrationMySql(_ConversationMySql):
    def __init__(self, error_code: int):
        super().__init__()
        self.error_code = error_code

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        if "ADD COLUMN is_pinned" in normalized or "ADD COLUMN pinned_at" in normalized:
            self.executions.append((normalized, params))
            raise _MySqlMigrationError(self.error_code, "pin migration failed")
        return super().execute(sql, params)


class _MigrationMemoryStore(InMemoryConversationStore):
    def __init__(self):
        super().__init__()
        self.restore_calls: list[tuple[str, str, str | None, bool, datetime | None]] = []

    def restore_conversation_metadata(
        self,
        conversation_id: str,
        actor_id: str,
        title: str | None,
        pinned: bool,
        pinned_at: datetime | None,
    ):
        self.restore_calls.append((conversation_id, actor_id, title, pinned, pinned_at))
        summary = self.update_conversation(conversation_id, actor_id, title=title, pinned=pinned)
        item = self.get(conversation_id, actor_id)
        if item is not None:
            item.pinned_at = pinned_at
        return summary


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


def test_memory_conversation_store_pins_updates_and_deletes_per_actor():
    store = InMemoryConversationStore()
    for conversation_id, actor_id in [("conv-old", "actor-1"), ("conv-new", "actor-1"), ("conv-other", "actor-2")]:
        store.append_message(
            conversation_id,
            actor_id,
            ConversationMessage(id=f"user-{conversation_id}", role="user", text=conversation_id),
        )

    pinned = store.update_conversation("conv-old", "actor-1", title="重要扫描", pinned=True)

    assert pinned is not None
    assert pinned.title == "重要扫描"
    assert pinned.pinned is True
    assert pinned.pinned_at is not None
    assert [item.conversation_id for item in store.list("actor-1")] == ["conv-old", "conv-new"]
    assert store.update_conversation("conv-old", "actor-2", pinned=False) is None
    assert store.delete_conversation("conv-old", "actor-2") is False
    assert store.delete_conversation("conv-old", "actor-1") is True
    assert store.get("conv-old", "actor-1") is None


def test_redis_conversation_store_persists_pin_metadata_and_deletes_both_keys():
    client = _ConversationRedis()
    message = ConversationMessage(id="user-1", role="user", text="原始标题")
    client.lists["supervisor:conversation:actor-1:conv-1:messages"] = [
        json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
    ]
    store = RedisConversationStore(client)

    updated = store.update_conversation("conv-1", "actor-1", title="置顶任务", pinned=True)
    restored = store.list("actor-1")[0]

    assert updated is not None
    assert restored.title == "置顶任务"
    assert restored.pinned is True
    assert restored.pinned_at is not None
    assert store.delete_conversation("conv-1", "actor-1") is True
    assert client.hgetall("supervisor:conversation:actor-1:conv-1:meta") == {}


def test_redis_duplicate_message_returns_persisted_metadata():
    client = _ConversationRedis()
    message = ConversationMessage(id="user-1", role="user", text="原始标题")
    client.lists["supervisor:conversation:actor-1:conv-1:messages"] = [
        json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
    ]
    client.hashes["supervisor:conversation:actor-1:conv-1:meta"] = {
        "title": "已保存标题",
        "pinned": "1",
        "pinned_at": "2026-08-25T10:30:00Z",
        "updated_at": "2026-08-25T10:30:00Z",
    }
    store = RedisConversationStore(client)

    restored = store.append_message("conv-1", "actor-1", message)

    assert restored.title == "已保存标题"
    assert restored.pinned is True
    assert restored.pinned_at == datetime(2026, 8, 25, 10, 30, tzinfo=timezone.utc)


def test_mysql_conversation_store_pin_contract():
    database = _ConversationMySql()
    store = MySqlConversationStore(database)
    schema_sql = [sql for sql, _params in database.executions]

    assert any("is_pinned TINYINT(1) NOT NULL DEFAULT 0" in sql for sql in schema_sql)
    assert any("pinned_at DATETIME(6) NULL" in sql for sql in schema_sql)
    assert "ALTER TABLE tg_agent_conversation ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0 AFTER title" in schema_sql
    assert "ALTER TABLE tg_agent_conversation ADD COLUMN pinned_at DATETIME(6) NULL AFTER is_pinned" in schema_sql

    updated = store.update_conversation("conv-target", "actor-1", title="置顶任务", pinned=True)
    listed = store.list("actor-1")
    unpinned = store.update_conversation("conv-target", "actor-1", pinned=False)
    missing = store.update_conversation("conv-missing", "actor-1", pinned=True)

    assert updated is not None
    assert updated.conversation_id == "conv-target"
    assert updated.title == "置顶任务"
    assert updated.pinned is True
    assert updated.pinned_at is not None
    assert [item.conversation_id for item in listed] == ["conv-target", "conv-newer"]
    assert unpinned is not None
    assert unpinned.pinned is False
    assert unpinned.pinned_at is None
    assert missing is None

    executed_sql = [sql for sql, _params in database.executions]
    assert any("is_pinned = %s, pinned_at = NOW(6)" in sql for sql in executed_sql)
    assert any("is_pinned = %s, pinned_at = NULL" in sql for sql in executed_sql)
    assert any("WHERE c.actor_id = %s AND c.conversation_id = %s" in sql for sql in executed_sql)
    assert any(
        "ORDER BY c.is_pinned DESC, c.pinned_at DESC, c.updated_at DESC, c.id DESC" in sql
        for sql in executed_sql
    )


def test_mysql_get_restores_conversation_metadata():
    database = _ConversationMySql()
    store = MySqlConversationStore(database)
    store.update_conversation("conv-target", "actor-1", title="置顶任务", pinned=True)

    restored = store.get("conv-target", "actor-1")

    assert restored is not None
    assert restored.title == "置顶任务"
    assert restored.pinned is True
    assert restored.pinned_at == datetime(2026, 8, 25, 10, 30, tzinfo=timezone.utc)
    assert restored.created_at == datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
    assert restored.updated_at == datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def test_mysql_restores_historical_pin_timestamp_for_cache_migration():
    database = _ConversationMySql()
    store = MySqlConversationStore(database)
    historical_pin = datetime(2026, 8, 20, 7, 15, tzinfo=timezone.utc)

    restored = store.restore_conversation_metadata(
        "conv-target",
        "actor-1",
        title="缓存标题",
        pinned=True,
        pinned_at=historical_pin,
    )

    assert restored is not None
    assert restored.title == "缓存标题"
    assert restored.pinned is True
    assert restored.pinned_at == "2026-08-20T07:15:00Z"


def test_mysql_pin_column_migration_ignores_duplicate_column_errors():
    database = _PinMigrationMySql(1060)

    MySqlConversationStore(database)

    executed_sql = [sql for sql, _params in database.executions]
    assert any("ADD COLUMN is_pinned" in sql for sql in executed_sql)
    assert any("ADD COLUMN pinned_at" in sql for sql in executed_sql)


def test_mysql_pin_column_migration_propagates_non_duplicate_errors():
    database = _PinMigrationMySql(1142)

    with pytest.raises(_MySqlMigrationError, match="pin migration failed"):
        MySqlConversationStore(database)


def test_conversation_patch_returns_target_summary_and_validates_body(monkeypatch):
    store = InMemoryConversationStore()
    monkeypatch.setattr(main, "_conversations", store)
    for conversation_id in ["conv-old", "conv-new"]:
        store.append_message(
            conversation_id,
            "actor-1",
            ConversationMessage(id=f"user-{conversation_id}", role="user", text=conversation_id),
        )
    client = TestClient(main.app)
    headers = {"X-Actor-Id": "actor-1"}

    pinned = client.patch("/v1/conversations/conv-old", headers=headers, json={"pinned": True})
    unpinned = client.patch("/v1/conversations/conv-old", headers=headers, json={"pinned": False})
    empty = client.patch("/v1/conversations/conv-old", headers=headers, json={})
    missing = client.patch("/v1/conversations/missing", headers=headers, json={"pinned": True})

    assert pinned.status_code == 200
    assert pinned.json()["conversationId"] == "conv-old"
    assert pinned.json()["pinned"] is True
    assert pinned.json()["pinnedAt"] is not None
    assert unpinned.json()["pinned"] is False
    assert unpinned.json()["pinnedAt"] is None
    assert empty.status_code == 422
    assert missing.status_code == 404


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


def test_mirrored_store_migrates_cached_metadata_to_durable_storage():
    durable = _MigrationMemoryStore()
    cache = InMemoryConversationStore()
    cache.append_message(
        "conv-legacy",
        "actor-1",
        ConversationMessage(id="user-1", role="user", text="原始标题"),
    )
    cache.update_conversation("conv-legacy", "actor-1", title="缓存标题", pinned=True)
    cached_pinned_at = cache.get("conv-legacy", "actor-1").pinned_at
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]

    migrated = store.get("conv-legacy", "actor-1")

    assert migrated is not None
    assert migrated.title == "缓存标题"
    assert migrated.pinned is True
    assert migrated.pinned_at == cached_pinned_at
    assert durable.list("actor-1")[0].title == "缓存标题"
    assert durable.list("actor-1")[0].pinned is True
    assert durable.restore_calls == [
        ("conv-legacy", "actor-1", "缓存标题", True, cached_pinned_at)
    ]


def test_mirrored_store_updates_cache_only_conversation_after_migration():
    durable = InMemoryConversationStore()
    cache = InMemoryConversationStore()
    cache.append_message(
        "conv-legacy",
        "actor-1",
        ConversationMessage(id="user-1", role="user", text="原始标题"),
    )
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]

    updated = store.update_conversation("conv-legacy", "actor-1", title="迁移后标题", pinned=True)

    assert updated is not None
    assert updated.title == "迁移后标题"
    assert updated.pinned is True
    assert durable.list("actor-1")[0].title == "迁移后标题"
    assert cache.list("actor-1")[0].title == "迁移后标题"


def test_mirrored_store_mirrors_pin_metadata_without_changing_durable_authority():
    durable = InMemoryConversationStore()
    cache = InMemoryConversationStore()
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]
    store.append_message(
        "conv-1",
        "actor-1",
        ConversationMessage(id="user-1", role="user", text="原始标题"),
    )

    pinned = store.update_conversation("conv-1", "actor-1", title="置顶任务", pinned=True)

    assert pinned is not None
    assert pinned.conversation_id == "conv-1"
    assert durable.list("actor-1")[0].pinned is True
    assert cache.list("actor-1")[0].pinned is True

    def fail_cache_update(*_args, **_kwargs):
        raise RuntimeError("cache unavailable")

    cache.update_conversation = fail_cache_update  # type: ignore[method-assign]
    renamed = store.update_conversation("conv-1", "actor-1", title="MySQL 权威标题")

    assert renamed is not None
    assert renamed.title == "MySQL 权威标题"
    assert durable.list("actor-1")[0].title == "MySQL 权威标题"
    assert cache.list("actor-1")[0].title == "置顶任务"


def test_mirrored_store_list_keeps_durable_authority_for_cached_item_beyond_limit():
    durable = _MigrationMemoryStore()
    durable.append_message(
        "conv-target",
        "actor-1",
        ConversationMessage(id="durable-1", role="user", text="权威消息"),
    )
    durable.update_conversation("conv-target", "actor-1", title="MySQL 权威标题")
    for index in range(50):
        durable.append_message(
            f"conv-newer-{index}",
            "actor-1",
            ConversationMessage(id=f"newer-{index}", role="user", text=f"较新会话 {index}"),
        )
    authoritative_updated_at = durable.get("conv-target", "actor-1").updated_at

    cache = InMemoryConversationStore()
    cache.append_message(
        "conv-target",
        "actor-1",
        ConversationMessage(id="cache-1", role="user", text="过期缓存消息"),
    )
    cache.update_conversation("conv-target", "actor-1", title="过期 Redis 标题", pinned=True)
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]

    listed = store.list("actor-1", limit=50)
    authoritative = durable.get("conv-target", "actor-1")

    assert len(listed) == 50
    assert "conv-target" not in [item.conversation_id for item in listed]
    assert authoritative is not None
    assert authoritative.title == "MySQL 权威标题"
    assert authoritative.pinned is False
    assert authoritative.updated_at == authoritative_updated_at
    assert [message.text for message in authoritative.messages] == ["权威消息"]
    assert durable.restore_calls == []


def test_mirrored_store_cache_delete_failure_preserves_durable_conversation():
    durable = _MigrationMemoryStore()
    cache = InMemoryConversationStore()
    message = ConversationMessage(id="user-1", role="user", text="保留会话")
    durable.append_message("conv-1", "actor-1", message)
    cache.append_message("conv-1", "actor-1", message)
    store = MirroredConversationStore(durable, cache)  # type: ignore[arg-type]

    def fail_cache_delete(*_args, **_kwargs):
        raise RuntimeError("cache delete failed")

    cache.delete_conversation = fail_cache_delete  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="cache delete failed"):
        store.delete_conversation("conv-1", "actor-1")

    assert durable.get("conv-1", "actor-1") is not None
    assert cache.get("conv-1", "actor-1") is not None
    assert store.get("conv-1", "actor-1") is durable.get("conv-1", "actor-1")
    assert durable.restore_calls == []


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


def test_alert_triage_draft_can_be_created_restored_and_consumed(monkeypatch):
    store = InMemoryConversationStore()
    monkeypatch.setattr(main, "_conversations", store)
    monkeypatch.setattr(main, "_drafts", DraftStore())
    client = TestClient(main.app)
    headers = {"X-Actor-Id": "actor-triage"}

    draft_response = client.post(
        "/v1/task-agent/draft",
        headers=headers,
        json={
            "message": "请研判告警 alert-tp-webshell-001，场景ID: webshell-true-positive",
            "workflowId": "auto",
        },
    )
    body = draft_response.json()
    consumed = client.post(
        "/v1/task-agent/drafts/consume",
        headers=headers,
        json={
            "confirmationToken": body["confirmationToken"],
            "idempotencyKey": "idem-triage",
        },
    )
    restored = client.get(f"/v1/conversations/{body['conversationId']}", headers=headers)

    assert draft_response.status_code == 200
    assert body["workflowId"] == "alert_triage"
    assert body["draft"]["alertUuid"] == "alert-tp-webshell-001"
    assert consumed.json()["workflowId"] == "alert_triage"
    assert consumed.json()["draft"]["scenarioId"] == "webshell-true-positive"
    assert restored.json()["messages"][1]["draft"]["workflowId"] == "alert_triage"


def test_alert_triage_missing_uuid_returns_clarification_instead_of_validation_error(monkeypatch):
    monkeypatch.setattr(main, "_conversations", InMemoryConversationStore())
    monkeypatch.setattr(main, "_drafts", DraftStore())
    response = TestClient(main.app).post(
        "/v1/task-agent/draft",
        headers={"X-Actor-Id": "actor-triage"},
        json={"message": "请做一次告警研判", "workflowId": "alert_triage"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "NEEDS_CLARIFICATION"
    assert response.json()["missingFields"] == ["alert_uuid"]
    assert response.json()["confirmationToken"] is None
