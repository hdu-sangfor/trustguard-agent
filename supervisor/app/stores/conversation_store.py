from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any, Callable, Protocol

from app.domain.models import ConversationMessage, ConversationSummary

log = logging.getLogger("trustguard.supervisor.conversations")


def _message_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc)
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _datetime_iso(value: Any) -> str:
    if not isinstance(value, datetime):
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _conversation_summary(item: "Conversation") -> ConversationSummary:
    first_user = next((message.text for message in item.messages if message.role == "user"), "新会话")
    latest = item.messages[-1] if item.messages else None
    task_message = next((message for message in reversed(item.messages) if message.task_id), None)
    status_message = next((message for message in reversed(item.messages) if message.task_status), None)
    return ConversationSummary(
        conversation_id=item.conversation_id,
        title=_compact_text(item.title or first_user, 200) or "新会话",
        preview=_compact_text(latest.text if latest else "", 500),
        task_id=task_message.task_id if task_message else None,
        task_status=status_message.task_status if status_message else None,
        message_count=len(item.messages),
        created_at=_datetime_iso(item.created_at),
        updated_at=_datetime_iso(item.updated_at),
        pinned=item.pinned,
        pinned_at=_datetime_iso(item.pinned_at) if item.pinned_at else None,
    )


@dataclass
class Conversation:
    conversation_id: str
    actor_id: str
    messages: list[ConversationMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_order: int = 0
    title: str | None = None
    pinned: bool = False
    pinned_at: datetime | None = None


class ConversationStore(Protocol):
    backend: str

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None: ...

    def list(self, actor_id: str, limit: int = 50) -> list[ConversationSummary]: ...

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation: ...

    def update_conversation(
        self, conversation_id: str, actor_id: str, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary | None: ...

    def delete_conversation(self, conversation_id: str, actor_id: str) -> bool: ...


class InMemoryConversationStore:
    backend = "memory"

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Conversation] = {}
        self._update_order = 0

    def get_or_create(self, conversation_id: str, actor_id: str) -> Conversation:
        key = (actor_id, conversation_id)
        item = self._items.get(key)
        if item is None:
            item = Conversation(conversation_id=conversation_id, actor_id=actor_id)
            self._items[key] = item
        return item

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        return self._items.get((actor_id, conversation_id))

    def list(self, actor_id: str, limit: int = 50) -> list[ConversationSummary]:
        items = sorted(
            (item for (owner, _conversation_id), item in self._items.items() if owner == actor_id),
            key=lambda item: (
                item.pinned,
                item.pinned_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
                item.updated_at,
                item.updated_order,
            ),
            reverse=True,
        )[:limit]
        return [_conversation_summary(item) for item in items]

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        item = self.get_or_create(conversation_id, actor_id)
        if not any(existing.id == message.id for existing in item.messages):
            item.messages.append(message)
        item.messages = item.messages[-200:]
        item.updated_at = datetime.now(timezone.utc)
        self._update_order += 1
        item.updated_order = self._update_order
        return item

    def update_conversation(
        self, conversation_id: str, actor_id: str, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary | None:
        item = self.get(conversation_id, actor_id)
        if item is None:
            return None
        if title is not None:
            item.title = title
        if pinned is not None:
            item.pinned = pinned
            item.pinned_at = datetime.now(timezone.utc) if pinned else None
        item.updated_at = datetime.now(timezone.utc)
        self._update_order += 1
        item.updated_order = self._update_order
        return _conversation_summary(item)

    def delete_conversation(self, conversation_id: str, actor_id: str) -> bool:
        return self._items.pop((actor_id, conversation_id), None) is not None


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
    def _meta_key(conversation_id: str, actor_id: str) -> str:
        return f"supervisor:conversation:{actor_id}:{conversation_id}:meta"

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
        messages = [self._decode(item) for item in raw_messages]
        timestamps = [_message_datetime(message.created_at) for message in messages]
        metadata = {
            key.decode() if isinstance(key, bytes) else str(key):
            value.decode() if isinstance(value, bytes) else str(value)
            for key, value in self._client.hgetall(self._meta_key(conversation_id, actor_id)).items()
        }
        pinned_value = metadata.get("pinned")
        pinned = True if pinned_value == "1" else False if pinned_value == "0" else False
        pinned_at_value = metadata.get("pinned_at")
        return Conversation(
            conversation_id=conversation_id,
            actor_id=actor_id,
            messages=messages,
            created_at=min(timestamps),
            updated_at=(
                _message_datetime(metadata["updated_at"])
                if metadata.get("updated_at")
                else max(timestamps)
            ),
            title=metadata.get("title") or None,
            pinned=pinned,
            pinned_at=_message_datetime(pinned_at_value) if pinned_at_value else None,
        )

    def list(self, actor_id: str, limit: int = 50) -> list[ConversationSummary]:
        prefix = f"supervisor:conversation:{actor_id}:"
        suffix = ":messages"
        conversations: list[Conversation] = []
        for raw_key in self._client.scan_iter(match=f"{prefix}*{suffix}", count=max(50, limit * 2)):
            key = raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key)
            if not key.startswith(prefix) or not key.endswith(suffix):
                continue
            conversation_id = key[len(prefix):-len(suffix)]
            item = self.get(conversation_id, actor_id)
            if item is None:
                continue
            conversations.append(item)
        conversations.sort(
            key=lambda item: (
                item.pinned,
                item.pinned_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
                item.updated_at,
            ),
            reverse=True,
        )
        return [_conversation_summary(item) for item in conversations[:limit]]

    def append_message(
        self, conversation_id: str, actor_id: str, message: ConversationMessage
    ) -> Conversation:
        key = self._key(conversation_id, actor_id)
        meta_key = self._meta_key(conversation_id, actor_id)
        serialized = json.dumps(message.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        # WATCH keeps deterministic message IDs idempotent across SSE reconnects.
        while True:
            try:
                with self._client.pipeline(transaction=True) as pipe:
                    pipe.watch(key)
                    existing = [self._decode(item) for item in pipe.lrange(key, 0, -1)]
                    if any(item.id == message.id for item in existing):
                        pipe.unwatch()
                        return self.get(conversation_id, actor_id) or Conversation(
                            conversation_id=conversation_id, actor_id=actor_id, messages=existing
                        )
                    pipe.multi()
                    pipe.rpush(key, serialized)
                    pipe.ltrim(key, -self._max_messages, -1)
                    pipe.hset(meta_key, mapping={"updated_at": _datetime_iso(datetime.now(timezone.utc))})
                    pipe.expire(key, self._ttl_seconds)
                    pipe.expire(meta_key, self._ttl_seconds)
                    pipe.execute()
                    break
            except Exception as exc:
                if exc.__class__.__name__ != "WatchError":
                    raise
        return self.get(conversation_id, actor_id) or Conversation(
            conversation_id=conversation_id, actor_id=actor_id, messages=[message]
        )

    def update_conversation(
        self, conversation_id: str, actor_id: str, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary | None:
        item = self.get(conversation_id, actor_id)
        if item is None:
            return None
        metadata = {"updated_at": _datetime_iso(datetime.now(timezone.utc))}
        if title is not None:
            metadata["title"] = title
        if pinned is not None:
            metadata["pinned"] = "1" if pinned else "0"
            metadata["pinned_at"] = _datetime_iso(datetime.now(timezone.utc)) if pinned else ""
        meta_key = self._meta_key(conversation_id, actor_id)
        self._client.hset(meta_key, mapping=metadata)
        self._client.expire(self._key(conversation_id, actor_id), self._ttl_seconds)
        self._client.expire(meta_key, self._ttl_seconds)
        updated = self.get(conversation_id, actor_id)
        return _conversation_summary(updated) if updated is not None else None

    def delete_conversation(self, conversation_id: str, actor_id: str) -> bool:
        return bool(
            self._client.delete(
                self._key(conversation_id, actor_id),
                self._meta_key(conversation_id, actor_id),
            )
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
                title VARCHAR(500) NULL,
                is_pinned TINYINT(1) NOT NULL DEFAULT 0,
                pinned_at DATETIME(6) NULL,
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
            # Migration: add title column if missing (for existing databases)
            try:
                cursor.execute("ALTER TABLE tg_agent_conversation ADD COLUMN title VARCHAR(500) NULL AFTER task_id")
            except Exception:
                pass  # Column already exists or not supported
            try:
                cursor.execute(
                    "ALTER TABLE tg_agent_conversation "
                    "ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0 AFTER title"
                )
            except Exception as exc:
                if not exc.args or exc.args[0] != 1060:
                    raise
            try:
                cursor.execute(
                    "ALTER TABLE tg_agent_conversation "
                    "ADD COLUMN pinned_at DATETIME(6) NULL AFTER is_pinned"
                )
            except Exception as exc:
                if not exc.args or exc.args[0] != 1060:
                    raise

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
                SELECT title, is_pinned, pinned_at, created_at, updated_at
                FROM tg_agent_conversation
                WHERE actor_id = %s AND conversation_id = %s
                """,
                (actor_id, conversation_id),
            )
            metadata = cursor.fetchone()
            if metadata is None:
                return None
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
        return Conversation(
            conversation_id=conversation_id,
            actor_id=actor_id,
            messages=messages,
            created_at=_message_datetime(metadata["created_at"]),
            updated_at=_message_datetime(metadata["updated_at"]),
            title=metadata.get("title") or None,
            pinned=bool(metadata.get("is_pinned")),
            pinned_at=(
                _message_datetime(metadata["pinned_at"])
                if metadata.get("pinned_at")
                else None
            ),
        )

    def _summaries(
        self, actor_id: str, limit: int | None = None, conversation_id: str | None = None
    ) -> list[ConversationSummary]:
        where_clause = "WHERE c.actor_id = %s"
        params: list[Any] = [actor_id]
        if conversation_id is not None:
            where_clause += " AND c.conversation_id = %s"
            params.append(conversation_id)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT %s"
            params.append(limit)
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT c.conversation_id, c.task_id, c.is_pinned, c.pinned_at,
                       c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count,
                       COALESCE(c.title, COALESCE((
                           SELECT first_user.message_text
                           FROM tg_agent_conversation_message first_user
                           WHERE first_user.actor_id = c.actor_id
                             AND first_user.conversation_id = c.conversation_id
                             AND first_user.role = 'user'
                           ORDER BY first_user.id ASC LIMIT 1
                       ), '新会话')) AS title,
                       COALESCE((
                           SELECT latest.message_text
                           FROM tg_agent_conversation_message latest
                           WHERE latest.actor_id = c.actor_id
                             AND latest.conversation_id = c.conversation_id
                           ORDER BY latest.id DESC LIMIT 1
                       ), '') AS preview,
                       (
                           SELECT latest_status.task_status
                           FROM tg_agent_conversation_message latest_status
                           WHERE latest_status.actor_id = c.actor_id
                             AND latest_status.conversation_id = c.conversation_id
                             AND latest_status.task_status IS NOT NULL
                           ORDER BY latest_status.id DESC LIMIT 1
                       ) AS task_status
                FROM tg_agent_conversation c
                LEFT JOIN tg_agent_conversation_message m
                  ON m.actor_id = c.actor_id AND m.conversation_id = c.conversation_id
                {where_clause}
                GROUP BY c.id, c.conversation_id, c.actor_id, c.task_id, c.title,
                         c.is_pinned, c.pinned_at, c.created_at, c.updated_at
                ORDER BY c.is_pinned DESC, c.pinned_at DESC, c.updated_at DESC, c.id DESC
                {limit_clause}
                """,
                tuple(params),
            )
            rows = list(cursor.fetchall() or [])
        return [
            ConversationSummary(
                conversation_id=str(row.get("conversation_id") or ""),
                title=_compact_text(row.get("title"), 200) or "新会话",
                preview=_compact_text(row.get("preview"), 500),
                task_id=row.get("task_id"),
                task_status=row.get("task_status"),
                message_count=int(row.get("message_count") or 0),
                created_at=_datetime_iso(row.get("created_at")),
                updated_at=_datetime_iso(row.get("updated_at")),
                pinned=bool(row.get("is_pinned")),
                pinned_at=_datetime_iso(row.get("pinned_at")) if row.get("pinned_at") else None,
            )
            for row in rows
        ]

    def list(self, actor_id: str, limit: int = 50) -> list[ConversationSummary]:
        return self._summaries(actor_id, limit=limit)

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

    def update_conversation(
        self, conversation_id: str, actor_id: str, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary | None:
        updates: list[str] = []
        params: list[Any] = []
        if title is not None:
            updates.append("title = %s")
            params.append(title)
        if pinned is not None:
            updates.append("is_pinned = %s")
            params.append(int(pinned))
            updates.append("pinned_at = NOW(6)" if pinned else "pinned_at = NULL")
        if not updates:
            summaries = self._summaries(actor_id, conversation_id=conversation_id)
            return summaries[0] if summaries else None
        params.extend([actor_id, conversation_id])
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE tg_agent_conversation SET {', '.join(updates)} WHERE actor_id = %s AND conversation_id = %s",
                params,
            )
        summaries = self._summaries(actor_id, conversation_id=conversation_id)
        return summaries[0] if summaries else None

    def restore_conversation_metadata(
        self,
        conversation_id: str,
        actor_id: str,
        title: str | None,
        pinned: bool,
        pinned_at: datetime | None,
    ) -> ConversationSummary | None:
        stored_pinned_at = None
        if pinned_at is not None:
            aware = pinned_at if pinned_at.tzinfo is not None else pinned_at.replace(tzinfo=timezone.utc)
            stored_pinned_at = aware.astimezone(timezone.utc).replace(tzinfo=None)
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE tg_agent_conversation
                SET title = %s, is_pinned = %s, pinned_at = %s
                WHERE actor_id = %s AND conversation_id = %s
                """,
                (title, int(pinned), stored_pinned_at, actor_id, conversation_id),
            )
        summaries = self._summaries(actor_id, conversation_id=conversation_id)
        return summaries[0] if summaries else None

    def delete_conversation(self, conversation_id: str, actor_id: str) -> bool:
        with self._connection_factory() as conn, conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM tg_agent_conversation_message WHERE actor_id = %s AND conversation_id = %s",
                (actor_id, conversation_id),
            )
            cursor.execute(
                "DELETE FROM tg_agent_conversation WHERE actor_id = %s AND conversation_id = %s",
                (actor_id, conversation_id),
            )
            return cursor.rowcount > 0


class MirroredConversationStore:
    """Writes MySQL first and mirrors Redis for hot-state compatibility."""

    backend = "mysql+redis"

    def __init__(self, durable: MySqlConversationStore, cache: RedisConversationStore) -> None:
        self._durable = durable
        self._cache = cache

    def _migrate_cached(self, conversation_id: str, actor_id: str) -> Conversation | None:
        durable = self._durable.get(conversation_id, actor_id)
        if durable is not None:
            return durable
        cached = self._cache.get(conversation_id, actor_id)
        if cached is None:
            return None
        for message in cached.messages:
            self._durable.append_message(conversation_id, actor_id, message)
        if cached.title is not None or cached.pinned:
            self._durable.restore_conversation_metadata(
                conversation_id,
                actor_id,
                title=cached.title,
                pinned=cached.pinned,
                pinned_at=cached.pinned_at,
            )
        return self._durable.get(conversation_id, actor_id)

    def get(self, conversation_id: str, actor_id: str) -> Conversation | None:
        durable = self._durable.get(conversation_id, actor_id)
        if durable is not None:
            return durable
        # One-time compatibility migration for conversations created before MySQL persistence.
        return self._migrate_cached(conversation_id, actor_id)

    def list(self, actor_id: str, limit: int = 50) -> list[ConversationSummary]:
        durable = self._durable.list(actor_id, limit)
        durable_ids = {item.conversation_id for item in durable}
        cached = self._cache.list(actor_id, limit)
        missing_ids = [item.conversation_id for item in cached if item.conversation_id not in durable_ids]
        for conversation_id in missing_ids:
            self._migrate_cached(conversation_id, actor_id)
        return self._durable.list(actor_id, limit) if missing_ids else durable

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

    def update_conversation(
        self, conversation_id: str, actor_id: str, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary | None:
        if self._durable.get(conversation_id, actor_id) is None:
            self._migrate_cached(conversation_id, actor_id)
        durable = self._durable.update_conversation(conversation_id, actor_id, title, pinned)
        if durable is None:
            return None
        try:
            self._cache.update_conversation(conversation_id, actor_id, title, pinned)
        except Exception:
            log.exception("failed to mirror conversation metadata to Redis")
        return durable

    def delete_conversation(self, conversation_id: str, actor_id: str) -> bool:
        try:
            self._cache.delete_conversation(conversation_id, actor_id)
        except Exception:
            log.exception("failed to delete conversation from Redis cache")
            raise
        return self._durable.delete_conversation(conversation_id, actor_id)


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
