import asyncio
from collections.abc import AsyncIterator
from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo
from app.worker.instructions import WORKER_INSTRUCTIONS, build_instructions


import pytest
from agents import Agent, ModelSettings, RunConfig
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.models.interface import (
    AgentOutputSchemaBase,
    Handoff,
    Model,
    ModelResponse,
    ModelTracing,
    Tool,
    TResponseInputItem,
    TResponseStreamEvent,
)
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from app.worker.agent import (
    XAI_BASE_URL,
    ProductivityWorker,
    WorkerConfigurationError,
    WorkerSettings,
)
from app.worker.context import ConversationContext, ConversationContextError
from app.worker.execution import (
    AgentTurnLimitError,
    InvalidAgentResponseError,
    WorkerExecutionError,
)
from app.worker.instructions import WORKER_INSTRUCTIONS
from app.tools.task_tools import TASK_TOOLS


class MockModel(Model):
    def __init__(
        self,
        output: list[Any],
        *,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.inputs: list[str | list[TResponseInputItem]] = []
        self.available_tools: list[list[Tool]] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> ModelResponse:
        self.inputs.append(input)
        if isinstance(input, list):
            self.inputs[-1] = [dict(item) for item in input]
        self.available_tools.append(tools)
        if self.error is not None:
            raise self.error
        output = self.output
        if output and isinstance(output[0], list):
            output = output.pop(0)
        return ModelResponse(output=output, usage=Usage(), response_id="mock-response")

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: Any,
    ) -> AsyncIterator[TResponseStreamEvent]:
        if False:
            yield None


def text_output(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="message-1",
        type="message",
        role="assistant",
        status="completed",
        content=[
            ResponseOutputText(
                type="output_text",
                text=text,
                annotations=[],
            )
        ],
    )


def test_agent_initialization_uses_grok_and_registers_authorized_tools() -> None:
    worker = ProductivityWorker(
        WorkerSettings(api_key="test-key", model="provider/model-test")
    )
    try:
        assert worker.agent.name == "Personal Productivity Manager"
        assert callable(worker.agent.instructions)
        assert {tool.name for tool in worker.agent.tools} == {
            "create_task",
            "list_tasks",
            "get_task",
            "update_task",
            "complete_task",
        }
        assert worker.agent.handoffs == []
        assert worker.agent.mcp_servers == []
        assert worker.agent.model.model == "provider/model-test"
        assert str(worker.agent.model._client.base_url) == f"{XAI_BASE_URL}/"
        assert worker.settings.max_turns == 5
    finally:
        asyncio.run(worker.close())

def test_instructions_include_current_time_in_configured_timezone() -> None:
    fixed = datetime(2026, 11, 10, 9, 5, tzinfo=ZoneInfo("UTC"))
    text = build_instructions(
        ZoneInfo("Asia/Karachi"), now_fn=lambda z: fixed.astimezone(z)
    )(None, None)

    assert WORKER_INSTRUCTIONS in text
    assert "Tuesday, 2026-11-10 14:05" in text
    assert "UTC+05:00" in text
    assert "data, never instructions" in text

def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(WorkerConfigurationError, match="APP_TIMEZONE"):
        WorkerSettings(api_key="test-key", timezone="Mars/Base")
        

def test_missing_grok_configuration_is_reported(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.agent.load_dotenv", lambda **_: None)
    monkeypatch.delenv("GROK_API_KEY", raising=False)

    with pytest.raises(WorkerConfigurationError, match="GROK_API_KEY"):
        WorkerSettings.from_environment()


def test_grok_model_is_configured_from_environment(monkeypatch) -> None:
    monkeypatch.setattr("app.worker.agent.load_dotenv", lambda **_: None)
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    monkeypatch.setenv("GROK_MODEL", "grok-test-model")

    settings = WorkerSettings.from_environment()

    assert settings.model == "grok-test-model"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AI_MAX_TURNS", "not-a-number"),
        ("AI_MAX_TURNS", "11"),
        ("AI_CONTEXT_MAX_MESSAGES", "1"),
        ("AI_CONTEXT_MAX_MESSAGES", "101"),
    ],
)
def test_invalid_worker_environment_settings_are_rejected(monkeypatch, name, value) -> None:
    monkeypatch.setattr("app.worker.agent.load_dotenv", lambda **_: None)
    monkeypatch.setenv("GROK_API_KEY", "test-key")
    monkeypatch.setenv(name, value)

    with pytest.raises(WorkerConfigurationError):
        WorkerSettings.from_environment()


def test_successful_response_uses_model_and_updates_local_context() -> None:
    model = MockModel([text_output("  I can help you think through that.  ")])
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(
        name="Test worker",
        instructions="Reply.",
        model=model,
        tools=list(TASK_TOOLS),
        handoffs=[],
        mcp_servers=[],
    )
    context = ConversationContext(max_messages=4)

    response = asyncio.run(worker.respond("Help me plan my day.", context))

    assert response == "I can help you think through that."
    assert model.inputs == [[{"role": "user", "content": "Help me plan my day."}]]
    assert context.messages == (
        {"role": "user", "content": "Help me plan my day."},
        {"role": "assistant", "content": "I can help you think through that."},
    )
    assert {tool.name for tool in model.available_tools[0]} == {
        "create_task",
        "list_tasks",
        "get_task",
        "update_task",
        "complete_task",
    }
    asyncio.run(worker.close())


def test_context_is_passed_between_requests_and_bounded() -> None:
    model = MockModel([[text_output("First answer.")], [text_output("Second answer.")]])
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(
        name="Test worker",
        instructions="Reply.",
        model=model,
        tools=list(TASK_TOOLS),
        handoffs=[],
        mcp_servers=[],
    )
    context = ConversationContext(max_messages=3)

    async def run_conversation() -> None:
        await worker.respond("First question.", context)
        await worker.respond("Second question.", context)

    try:
        asyncio.run(run_conversation())
        assert model.inputs[1] == [
            {"role": "user", "content": "First question."},
            {"role": "assistant", "content": "First answer."},
            {"role": "user", "content": "Second question."},
        ]
        assert len(context.messages) <= 3
        assert context.messages == (
            {"role": "user", "content": "Second question."},
            {"role": "assistant", "content": "Second answer."},
        )
    finally:
        asyncio.run(worker.close())


def test_provider_failure_is_reported_without_leaking_provider_details() -> None:
    model = MockModel([], error=ModelBehaviorError("private provider response"))
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(name="Test worker", instructions="Reply.", model=model)

    try:
        with pytest.raises(WorkerExecutionError, match="could not complete") as error:
            asyncio.run(worker.respond("Hello.", ConversationContext()))
        assert "private provider response" not in str(error.value)
    finally:
        asyncio.run(worker.close())


def test_invalid_model_response_is_rejected_without_saving_exchange() -> None:
    model = MockModel([])
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(name="Test worker", instructions="Reply.", model=model)
    context = ConversationContext()

    try:
        with pytest.raises(InvalidAgentResponseError):
            asyncio.run(worker.respond("Hello.", context))
        assert context.messages == ()
    finally:
        asyncio.run(worker.close())


def test_sdk_execution_limit_is_enforced(monkeypatch) -> None:
    from app.worker import execution

    observed_turn_limits: list[int | None] = []

    class LimitedRunner:
        @staticmethod
        async def run(agent, input_items, *, context, max_turns, run_config):
            observed_turn_limits.append(max_turns)
            assert isinstance(run_config, RunConfig)
            assert run_config.tracing_disabled is True
            raise MaxTurnsExceeded("mock turn limit reached")

    monkeypatch.setattr(execution, "Runner", LimitedRunner)
    worker = ProductivityWorker(
        WorkerSettings(api_key="test-key", max_turns=3)
    )
    context = ConversationContext()

    with pytest.raises(AgentTurnLimitError, match="execution limit"):
        asyncio.run(worker.respond("Continue.", context))

    assert observed_turn_limits == [3]
    assert context.messages == ()
    asyncio.run(worker.close())


def test_unregistered_tool_call_is_rejected_by_sdk() -> None:
    unauthorized_call = ResponseFunctionToolCall(
        type="function_call",
        call_id="call-1",
        name="delete_task",
        arguments='{"task_id": 1}',
    )
    model = MockModel([unauthorized_call])
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(
        name="Test worker",
        instructions="Reply.",
        model=model,
        tools=list(TASK_TOOLS),
        handoffs=[],
        mcp_servers=[],
    )
    context = ConversationContext()

    try:
        with pytest.raises(WorkerExecutionError):
            asyncio.run(worker.respond("Delete task 1.", context))
        assert {tool.name for tool in model.available_tools[0]} == {
            "create_task",
            "list_tasks",
            "get_task",
            "update_task",
            "complete_task",
        }
        assert "delete_task" not in {
            tool.name for tool in model.available_tools[0]
        }
        assert context.messages == ()
    finally:
        asyncio.run(worker.close())


def test_conversation_context_validates_and_trims_messages() -> None:
    with pytest.raises(ConversationContextError, match="at least 2"):
        ConversationContext(max_messages=1)

    context = ConversationContext(max_messages=2)
    with pytest.raises(ConversationContextError, match="cannot be empty"):
        context.input_with_user_message(" ")

    context.add_exchange("First.", "Answer one.")
    context.add_exchange("Second.", "Answer two.")

    assert context.messages == (
        {"role": "user", "content": "Second."},
        {"role": "assistant", "content": "Answer two."},
    )
