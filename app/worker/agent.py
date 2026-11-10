import os
from dataclasses import dataclass

from agents import Agent
from agents.models.interface import Tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.tools.task_tools import TASK_TOOLS, TaskToolContext
from app.worker.context import ConversationContext
from app.worker.execution import execute_request
from app.worker.instructions import WORKER_INSTRUCTIONS
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.worker.instructions import build_instructions

XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4.7"
DEFAULT_MAX_TURNS = 5
DEFAULT_TIMEZONE = "UTC"
DEFAULT_CONTEXT_MAX_MESSAGES = 20
MAX_ALLOWED_TURNS = 10
MAX_ALLOWED_CONTEXT_MESSAGES = 100
AUTHORIZED_TOOLS: tuple[Tool, ...] = TASK_TOOLS


class WorkerConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class WorkerSettings:
    api_key: str
    model: str = DEFAULT_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    context_max_messages: int = DEFAULT_CONTEXT_MAX_MESSAGES
    timezone: str = DEFAULT_TIMEZONE

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        model = self.model.strip()
        if not api_key:
            raise WorkerConfigurationError("GROK_API_KEY is required.")
        if not model:
            raise WorkerConfigurationError("GROK_MODEL cannot be empty.")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        if not 1 <= self.max_turns <= MAX_ALLOWED_TURNS:
            raise WorkerConfigurationError(
                f"AI_MAX_TURNS must be between 1 and {MAX_ALLOWED_TURNS}."
            )
        if not 2 <= self.context_max_messages <= MAX_ALLOWED_CONTEXT_MESSAGES:
            raise WorkerConfigurationError(
                "AI_CONTEXT_MAX_MESSAGES must be between 2 and "
                f"{MAX_ALLOWED_CONTEXT_MESSAGES}."
            )
        try:
            ZoneInfo(self.timezone.strip())
        except (ZoneInfoNotFoundError, ValueError):
            raise WorkerConfigurationError(
                "APP_TIMEZONE must be a valid IANA timezone, e.g. Asia/Karachi."
            ) from None
        object.__setattr__(self, "timezone", self.timezone.strip())

    @classmethod
    def from_environment(cls) -> "WorkerSettings":
        load_dotenv(override=False)
        api_key = os.getenv("GROK_API_KEY", "").strip()
        if not api_key:
            raise WorkerConfigurationError(
                "GROK_API_KEY is not configured. Add it to the environment "
                "or your local .env file."
            )
        return cls(
            api_key=api_key,
            model=os.getenv("GROK_MODEL", DEFAULT_MODEL).strip(),
            max_turns=_environment_integer("AI_MAX_TURNS", DEFAULT_MAX_TURNS),
            timezone=os.getenv("APP_TIMEZONE", DEFAULT_TIMEZONE),
            context_max_messages=_environment_integer(
                "AI_CONTEXT_MAX_MESSAGES", DEFAULT_CONTEXT_MAX_MESSAGES
            ),
        )


def _environment_integer(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise WorkerConfigurationError(f"{name} must be an integer.") from exc


class ProductivityWorker:
    def __init__(self, settings: WorkerSettings | None = None) -> None:
        self.settings = settings or WorkerSettings.from_environment()
        self._client = AsyncOpenAI(
            api_key=self.settings.api_key,
            base_url=XAI_BASE_URL,
        )
        model = OpenAIChatCompletionsModel(
            model=self.settings.model,
            openai_client=self._client,
        )
        self.agent = Agent(
            name="Personal Productivity Manager",
            instructions=build_instructions(ZoneInfo(self.settings.timezone)),
            model=model,
            tools=list(AUTHORIZED_TOOLS),
            handoffs=[],
            mcp_servers=[],
        )

    async def respond(
        self,
        user_message: str,
        conversation: ConversationContext,
        session: Session | None = None,
    ) -> str:
        if not isinstance(conversation, ConversationContext):
            raise TypeError("conversation must be a ConversationContext.")
        return await execute_request(
            agent=self.agent,
            user_message=user_message,
            conversation=conversation,
            max_turns=self.settings.max_turns,
            tool_context=TaskToolContext(session=session),
        )

    async def close(self) -> None:
        await self._client.close()
