import asyncio
from threading import Lock

from app.worker.context import ConversationContext

MAX_ACTIVE_CONVERSATIONS = 1000


class ConversationCapacityError(RuntimeError):
    pass


class ConversationStore:
    def __init__(
        self,
        *,
        max_messages: int,
        max_conversations: int = MAX_ACTIVE_CONVERSATIONS,
    ) -> None:
        if max_conversations < 1:
            raise ValueError("max_conversations must be at least 1.")
        self._max_messages = max_messages
        self._max_conversations = max_conversations
        self._contexts: dict[str, ConversationContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = Lock()

    def get_or_create(
        self, conversation_id: str
    ) -> tuple[ConversationContext, asyncio.Lock]:
        with self._registry_lock:
            context = self._contexts.get(conversation_id)
            if context is not None:
                return context, self._locks[conversation_id]
            if len(self._contexts) >= self._max_conversations:
                raise ConversationCapacityError(
                    "The active conversation limit has been reached."
                )
            context = ConversationContext(max_messages=self._max_messages)
            lock = asyncio.Lock()
            self._contexts[conversation_id] = context
            self._locks[conversation_id] = lock
            return context, lock
