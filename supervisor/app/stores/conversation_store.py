from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Conversation:
    conversation_id: str
    actor_id: str
    messages: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}

    def get_or_create(self, conversation_id: str, actor_id: str) -> Conversation:
        item = self._items.get(conversation_id)
        if item is None or item.actor_id != actor_id:
            item = Conversation(conversation_id=conversation_id, actor_id=actor_id)
            self._items[conversation_id] = item
        return item

    def append(self, conversation_id: str, actor_id: str, message: str) -> Conversation:
        item = self.get_or_create(conversation_id, actor_id)
        item.messages.append(message.strip())
        item.messages = item.messages[-12:]
        item.updated_at = datetime.now(timezone.utc)
        return item
