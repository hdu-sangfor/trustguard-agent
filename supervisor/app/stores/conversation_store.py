from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any, Callable, Protocol

from app.domain.models import ConversationMessage

log = logging.getLogger("trustguard.supervisor.conversations")


@dataclass
class Conversation:
    conversation_id: str
    actor_id: str
    messages: list[ConversationMessage] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationStore(Protocol):
    backend: str

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None: ...

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation: ...


class InMemoryConversationStore:
    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Conversation] = {}

    def get_or_create(self, conversation_id: str, actor_id: str) -> Conversation:
        key = (actor_id, conversation_id)
        item = self._items.get(key)
        if item is None:
            item = Conversation(conversation_id=conversation_id, actor_id=actor_id)
            self._items[key] = item
        return item

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        return self._items.get((actor_id, conversation_id))

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        item = self.get_or_create(conversation_id, actor_id)
        if not any(existing.id == message.id for existing in item.messages):
            item.messages.append(message)
        item.messages = item.messages[-200:]
        item.updated_at = datetime.now(timezone.utc)
        return item


class RedisConversationStore:
    backend = "redis"

    def __init__(self, client: Any, ttl_seconds: int = 2592000, max_messages: int = 200) -> None:
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._max_messages = max_messages

    @staticmethod
    def _key(conversation_id: str, actor_id: str) -> str:
        return f"supervisor:conversation:{actor_id}:{conversation_id}:messages"

    @staticmethod
    def _decode(raw: Any) -> ConversationMessage:
        text = str(raw)
        try:
            return ConversationMessage.model_validate(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            # Compatibility with the former store, which kept plain user strings.
            digest = hashlib.sha256(text.encode()).hexdigest()[:24]
            return ConversationMessage(id=f"legacy-{digest}", role="user", text=text)

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        key = self._key(conversation_id, actor_id)
        raw_messages = self._client.lrange(key, 0, -1)
        if not raw_messages:
            return None
        return Conversation(
            conversation_id=conversation_id,
            actor_id=actor_id,
            messages=[self._decode(item) for item in raw_messages],
        )

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        key = self._key(conversation_id, actor_id)
        serialized = json.dumps(message.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        # WATCH keeps deterministic message IDs idempotent across SSE reconnects.
        while True:
            try:
                with self._client.pipeline(transaction=True) as pipe:
                    pipe.watch(key)
                    existing = [self._decode(item) for item in pipe.lrange(key, 0, -1)]
                    if any(item.id == message.id for item in existing):
                        pipe.unwatch()
                        return Conversation(conversation_id=conversation_id, actor_id=actor_id, messages=existing)
                    pipe.multi()
                    pipe.rpush(key, serialized)
                    pipe.ltrim(key, -self._max_messages, -1)
                    pipe.expire(key, self._ttl_seconds)
                    pipe.execute()
                    break
            except Exception as exc:
                if exc.__class__.__name__ != "WatchError":
                    raise
        return self.get(conversation_id, actor_id) or Conversation(
            conversation_id=conversation_id, actor_id=actor_id, messages=[message]
        )


class MySqlConversationStore:
    """Durable conversation store. Redis may mirror this store, but MySQL is authoritative."""

    backend = "mysql"

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS tg_agent_conversation (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                conversation_id VARCHAR(128) NOT NULL,
                actor_id VARCHAR(128) NOT NULL,
                task_id VARCHAR(128) NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                UNIQUE KEY uk_agent_conversation_actor (actor_id, conversation_id),
                INDEX idx_agent_conversation_task (task_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS tg_agent_conversation_message (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                message_id VARCHAR(160) NOT NULL,
                conversation_id VARCHAR(128) NOT NULL,
                actor_id VARCHAR(128) NOT NULL,
                role VARCHAR(16) NOT NULL,
                message_text MEDIUMTEXT NOT NULL,
                activities_json JSON NULL,
                draft_json JSON NULL,
                confirmation_token TEXT NULL,
                task_id VARCHAR(128) NULL,
                task_status VARCHAR(32) NULL,
                created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                UNIQUE KEY uk_agent_message_actor (actor_id, conversation_id, message_id),
                INDEX idx_agent_message_conversation (actor_id, conversation_id, id),
                INDEX idx_agent_message_task (task_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
        )
        with self._connection_factory() as conn, conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    @staticmethod
    def _created_at(value: str) -> datetime:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _json(value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _loads(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except (TypeError, ValueError):
            return default

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT message_id, role, message_text, activities_json, draft_json,
                       confirmation_token, task_id, task_status, created_at
                FROM tg_agent_conversation_message
                WHERE actor_id = %s AND conversation_id = %s
                ORDER BY id ASC
                """,
                (actor_id, conversation_id),
            )
            rows = list(cursor.fetchall() or [])
        if not rows:
            return None
        messages = [
            ConversationMessage.model_validate(
                {
                    "id": row.get("message_id"),
                    "role": row.get("role"),
                    "text": row.get("message_text") or "",
                    "activities": self._loads(row.get("activities_json"), []),
                    "draft": self._loads(row.get("draft_json"), None),
                    "confirmation_token": row.get("confirmation_token"),
                    "task_id": row.get("task_id"),
                    "task_status": row.get("task_status"),
                    "created_at": row["created_at"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            )
            for row in rows
        ]
        return Conversation(conversation_id=conversation_id, actor_id=actor_id, messages=messages)

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        draft = message.draft.model_dump(mode="json") if message.draft is not None else None
        activities = [activity.model_dump(mode="json") for activity in message.activities]
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tg_agent_conversation
                    (conversation_id, actor_id, task_id, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(6), NOW(6))
                ON DUPLICATE KEY UPDATE
                    task_id = COALESCE(VALUES(task_id), task_id), updated_at = NOW(6)
                """,
                (conversation_id, actor_id, message.task_id),
            )
            cursor.execute(
                """
                INSERT IGNORE INTO tg_agent_conversation_message
                    (message_id, conversation_id, actor_id, role, message_text,
                     activities_json, draft_json, confirmation_token, task_id,
                     task_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message.id,
                    conversation_id,
                    actor_id,
                    message.role,
                    message.text,
                    self._json(activities),
                    self._json(draft),
                    message.confirmation_token,
                    message.task_id,
                    message.task_status,
                    self._created_at(message.created_at),
                ),
            )
        return self.get(conversation_id, actor_id) or Conversation(
            conversation_id=conversation_id, actor_id=actor_id, messages=[message]
        )


class MirroredConversationStore:
    """Writes MySQL first and mirrors Redis for hot-state compatibility."""

    backend = "mysql+redis"

    def __init__(self, durable: MySqlConversationStore, cache: RedisConversationStore) -> None:
        self._durable = durable
        self._cache = cache

    def _migrate_cached(self, conversation_id: str, actor_id: str) -> Conversation | None:
        cached = self._cache.get(conversation_id, actor_id)
        if cached is None:
            return None
        for message in cached.messages:
            self._durable.append_message(conversation_id, actor_id, message)
        return self._durable.get(conversation_id, actor_id)

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        durable = self._durable.get(conversation_id, actor_id)
        if durable is not None:
            return durable
        # One-time compatibility migration for conversations created before MySQL persistence.
        return self._migrate_cached(conversation_id, actor_id)

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        if self._durable.get(conversation_id, actor_id) is None:
            self._migrate_cached(conversation_id, actor_id)
        durable = self._durable.append_message(conversation_id, actor_id, message)
        try:
            self._cache.append_message(conversation_id, actor_id, message)
        except Exception:
            log.exception("failed to mirror conversation message to Redis")
        return durable


def _redis_store_from_env() -> RedisConversationStore:
    import redis

    client = redis.Redis.from_url(
        os.getenv("REDIS_URL") or "redis://localhost:6379/0",
        decode_responses=True,
    )
    client.ping()
    return RedisConversationStore(
        client,
        ttl_seconds=int(os.getenv("SUPERVISOR_CONVERSATION_TTL_SECONDS") or "2592000"),
        max_messages=int(os.getenv("SUPERVISOR_CONVERSATION_MAX_MESSAGES") or "200"),
    )


def _mysql_store_from_env() -> MySqlConversationStore:
    import pymysql
    from pymysql.cursors import DictCursor

    def connection() -> Any:
        return pymysql.connect(
            host=os.getenv("MYSQL_HOST") or "127.0.0.1",
            port=int(os.getenv("MYSQL_PORT") or "3306"),
            user=os.getenv("MYSQL_USER") or "trustguard",
            password=os.getenv("MYSQL_PASSWORD") or "trustguard",
            database=os.getenv("MYSQL_DATABASE") or "trustguard_agent",
            charset="utf8mb4",
            autocommit=True,
            cursorclass=DictCursor,
        )

    return MySqlConversationStore(connection)


def create_conversation_store_from_env() -> ConversationStore:
    backend = (
        os.getenv("SUPERVISOR_CONVERSATION_STORE_BACKEND")
        or os.getenv("SUPERVISOR_STORE_BACKEND")
        or "memory"
    ).strip().lower()
    if backend == "memory":
        return InMemoryConversationStore()
    try:
        if backend == "redis":
            return _redis_store_from_env()
        if backend == "mysql":
            return _mysql_store_from_env()
        if backend in {"mysql+redis", "redis+mysql"}:
            return MirroredConversationStore(_mysql_store_from_env(), _redis_store_from_env())
        raise ValueError(f"unsupported conversation store backend: {backend}")
    except Exception:
        if (os.getenv("DEPLOYMENT_MODE") or "").strip().lower() == "docker":
            raise
        return InMemoryConversationStore()
