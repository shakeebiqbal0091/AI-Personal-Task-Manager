import logging
from typing import Any

from agents import Agent, RunConfig, Runner
from agents.exceptions import AgentsException, MaxTurnsExceeded
from openai import OpenAIError

from app.worker.context import ConversationContext
from app.tools.task_tools import TaskToolContext

logger = logging.getLogger(__name__)


class WorkerExecutionError(RuntimeError):
    pass


class AgentTurnLimitError(WorkerExecutionError):
    pass


class InvalidAgentResponseError(WorkerExecutionError):
    pass


async def execute_request(
    *,
    agent: Agent[Any],
    user_message: str,
    conversation: ConversationContext,
    max_turns: int,
    tool_context: TaskToolContext | None = None,
) -> str:
    input_items = conversation.input_with_user_message(user_message)
    try:
        result = await Runner.run(
            agent,
            input_items,
            context=tool_context or TaskToolContext(),
            max_turns=max_turns,
            run_config=RunConfig(tracing_disabled=True),
        )
    except MaxTurnsExceeded as exc:
        raise AgentTurnLimitError(
            "The AI response exceeded the configured execution limit."
        ) from exc
    except (AgentsException, OpenAIError) as exc:
        status_code = getattr(exc, "status_code", None)
        logger.warning(
            "AI worker execution failed (%s, provider_status_code=%s)",
            type(exc).__name__,
            status_code,
        )
        raise WorkerExecutionError(
            "The AI provider could not complete the request. Please try again later."
        ) from exc

    response = result.final_output
    if not isinstance(response, str) or not response.strip():
        raise InvalidAgentResponseError(
            "The AI provider returned an empty or invalid response."
        )

    response = response.strip()
    conversation.add_exchange(user_message, response)
    return response
