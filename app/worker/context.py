from dataclasses import dataclass, field

from agents.models.interface import TResponseInputItem

MAX_MESSAGE_LENGTH = 10_000


class ConversationContextError(ValueError):
    pass


@dataclass
class ConversationContext:
    max_messages: int = 20
    _messages: list[TResponseInputItem] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.max_messages < 2:
            raise ConversationContextError("max_messages must be at least 2.")

    @property
    def messages(self) -> tuple[TResponseInputItem, ...]:
        return tuple(self._messages)

    def input_with_user_message(self, user_message: str) -> list[TResponseInputItem]:
        message = self._validate_message(user_message)
        return self._bounded(
            [*self._messages, {"role": "user", "content": message}]
        )

    def add_exchange(self, user_message: str, assistant_message: str) -> None:
        user = self._validate_message(user_message)
        assistant = self._validate_message(assistant_message)
        self._messages = self._bounded(
            [
                *self._messages,
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        )

    @staticmethod
    def _validate_message(message: str) -> str:
        if not isinstance(message, str) or not message.strip():
            raise ConversationContextError("Conversation messages cannot be empty.")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise ConversationContextError(
                f"Conversation messages cannot exceed {MAX_MESSAGE_LENGTH} characters."
            )
        return message.strip()

    def _bounded(self, messages: list[TResponseInputItem]) -> list[TResponseInputItem]:
        bounded = messages[-self.max_messages :]
        if bounded and bounded[0].get("role") == "assistant":
            bounded = bounded[1:]
        return bounded
