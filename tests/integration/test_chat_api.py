import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from agents import Agent, ModelSettings
from agents.exceptions import ModelBehaviorError
from agents.models.interface import (
    AgentOutputSchemaBase,
    Handoff,
    Model,
    ModelResponse,
    ModelTracing,
    TResponseInputItem,
    TResponseStreamEvent,
    Tool,
)
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.chat import ChatRuntime, get_chat_runtime
from app.database.database import get_db
from app.database.models import Task
from app.main import app
from app.schemas.task import TaskCreate
from app.services import task_service
from app.tools.task_tools import TASK_TOOLS
from app.worker.agent import ProductivityWorker, WorkerSettings
from app.worker.conversations import ConversationStore


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


class ScriptedModel(Model):
    def __init__(
        self,
        outputs: list[list[Any]],
        *,
        error: Exception | None = None,
    ) -> None:
        self.outputs = outputs
        self.error = error
        self.inputs: list[list[TResponseInputItem]] = []

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
        self.inputs.append([dict(item) for item in input] if isinstance(input, list) else [])
        if self.error is not None:
            raise self.error
        return ModelResponse(
            output=self.outputs.pop(0),
            usage=Usage(),
            response_id="mock-response",
        )

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


class ConcurrentModel(Model):
    def __init__(self) -> None:
        self.inputs: list[list[TResponseInputItem]] = []
        self._arrivals = 0
        self._both_arrived = asyncio.Event()

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
        items = [dict(item) for item in input] if isinstance(input, list) else []
        self.inputs.append(items)
        self._arrivals += 1
        if self._arrivals == 2:
            self._both_arrived.set()
        await asyncio.wait_for(self._both_arrived.wait(), timeout=2)
        user_message = next(
            item["content"] for item in items if item.get("role") == "user"
        )
        return ModelResponse(
            output=[text_output(f"Response to {user_message}")],
            usage=Usage(),
            response_id="mock-response",
        )

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


@pytest.fixture
def runtime_factory() -> Any:
    workers: list[ProductivityWorker] = []

    def create(
        model: Model,
        *,
        max_messages: int = 20,
        max_conversations: int = 1000,
    ) -> tuple[ChatRuntime, ProductivityWorker]:
        worker = ProductivityWorker(
            WorkerSettings(api_key="test-key", context_max_messages=max_messages)
        )
        worker.agent = Agent(
            name="Personal Productivity Manager",
            instructions="Use the authorized tools.",
            model=model,
            tools=list(TASK_TOOLS),
            handoffs=[],
            mcp_servers=[],
        )
        workers.append(worker)
        return (
            ChatRuntime(
                worker,
                ConversationStore(
                    max_messages=max_messages,
                    max_conversations=max_conversations,
                ),
            ),
            worker,
        )

    yield create
    for worker in workers:
        asyncio.run(worker.close())


def install_runtime(runtime: ChatRuntime) -> None:
    app.dependency_overrides[get_chat_runtime] = lambda: runtime


def tool_call(name: str, arguments: dict[str, object]) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        id="tool-call-1",
        type="function_call",
        call_id="call-1",
        name=name,
        arguments=json.dumps(arguments),
    )


def test_chat_returns_worker_response_and_keeps_context(client, runtime_factory) -> None:
    model = ScriptedModel(
        [[text_output("First answer.")], [text_output("Second answer.")]]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    first_status, first = client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "daily", "message": "First question"},
    )
    second_status, second = client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "daily", "message": "Second question"},
    )

    assert first_status == second_status == 200
    assert first == {"conversation_id": "daily", "response": "First answer."}
    assert second == {"conversation_id": "daily", "response": "Second answer."}
    assert model.inputs[1] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer."},
        {"role": "user", "content": "Second question"},
    ]


def test_chat_creates_task_through_existing_worker_tool(client, runtime_factory) -> None:
    model = ScriptedModel(
        [
            [tool_call("create_task", {"title": "Plan the release"})],
            [text_output("The release task was created.")],
        ]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    status_code, response = client.request(
        "POST",
        "/chat",
        json_body={
            "conversation_id": "create",
            "message": "Create a task to plan the release.",
        },
    )

    assert status_code == 200
    assert response["response"] == "The release task was created."
    assert any(
        item.get("type") == "function_call_output" for item in model.inputs[1]
    )
    assert client.request("GET", "/tasks")[1][0]["title"] == "Plan the release"


def test_chat_retrieves_task_through_existing_worker_tool(
    client, db_session: Session, runtime_factory
) -> None:
    task = task_service.create_task(db_session, TaskCreate(title="Review design"))
    model = ScriptedModel(
        [
            [tool_call("get_task", {"task_id": task.id})],
            [text_output("The task is Review design.")],
        ]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    status_code, response = client.request(
        "POST",
        "/chat",
        json_body={
            "conversation_id": "retrieve",
            "message": f"Show task {task.id}.",
        },
    )

    assert status_code == 200
    assert response["response"] == "The task is Review design."
    assert any(
        item.get("type") == "function_call_output" for item in model.inputs[1]
    )


def test_chat_contexts_are_separate_by_conversation_id(client, runtime_factory) -> None:
    model = ScriptedModel(
        [
            [text_output("A answer.")],
            [text_output("B answer.")],
            [text_output("A follow-up.")],
        ]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    for conversation_id, message in (
        ("conversation-a", "A private context"),
        ("conversation-b", "B private context"),
        ("conversation-a", "Continue A"),
    ):
        assert client.request(
            "POST",
            "/chat",
            json_body={"conversation_id": conversation_id, "message": message},
        )[0] == 200

    assert model.inputs[2] == [
        {"role": "user", "content": "A private context"},
        {"role": "assistant", "content": "A answer."},
        {"role": "user", "content": "Continue A"},
    ]
    assert "B private context" not in str(model.inputs[2])


def test_chat_history_respects_configured_message_limit(client, runtime_factory) -> None:
    model = ScriptedModel(
        [
            [text_output("First.")],
            [text_output("Second.")],
        ]
    )
    runtime, _ = runtime_factory(model, max_messages=2)
    install_runtime(runtime)

    for message in ("First request", "Second request"):
        assert client.request(
            "POST",
            "/chat",
            json_body={"conversation_id": "bounded", "message": message},
        )[0] == 200

    context, _ = runtime.conversations.get_or_create("bounded")
    assert len(context.messages) <= 2
    assert model.inputs[1] == [{"role": "user", "content": "Second request"}]


def test_concurrent_conversations_do_not_share_context(client, runtime_factory) -> None:
    model = ConcurrentModel()
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    async def send_both() -> list[tuple[int, dict[str, Any] | None]]:
        return list(
            await asyncio.gather(
                client.async_request(
                    "POST",
                    "/chat",
                    json_body={"conversation_id": "left", "message": "left secret"},
                ),
                client.async_request(
                    "POST",
                    "/chat",
                    json_body={"conversation_id": "right", "message": "right secret"},
                ),
            )
        )

    results = asyncio.run(send_both())

    assert all(status == 200 for status, _ in results)
    assert len(model.inputs) == 2
    assert all(len(items) == 1 for items in model.inputs)
    assert {items[0]["content"] for items in model.inputs} == {
        "left secret",
        "right secret",
    }


def test_concurrent_requests_for_one_conversation_are_serialized(
    client, runtime_factory
) -> None:
    model = ScriptedModel(
        [
            [text_output("First response.")],
            [text_output("Second response.")],
        ]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    async def send_both() -> list[tuple[int, dict[str, Any] | None]]:
        return list(
            await asyncio.gather(
                client.async_request(
                    "POST",
                    "/chat",
                    json_body={"conversation_id": "shared", "message": "First"},
                ),
                client.async_request(
                    "POST",
                    "/chat",
                    json_body={"conversation_id": "shared", "message": "Second"},
                ),
            )
        )

    results = asyncio.run(send_both())

    assert all(status == 200 for status, _ in results)
    assert len(model.inputs) == 2
    first_message = model.inputs[0][0]["content"]
    assert model.inputs[1][:2] == [
        {"role": "user", "content": first_message},
        {"role": "assistant", "content": "First response."},
    ]
    assert model.inputs[1][-1]["role"] == "user"
    assert model.inputs[1][-1]["content"] != first_message
    assert len(runtime.conversations.get_or_create("shared")[0].messages) == 4


def test_chat_rejects_missing_and_invalid_input(client, runtime_factory) -> None:
    runtime, _ = runtime_factory(ScriptedModel([[text_output("Unused.")]]))
    install_runtime(runtime)

    assert client.request("POST", "/chat", json_body={"message": "Hello"})[0] == 422
    assert (
        client.request(
            "POST",
            "/chat",
            json_body={"conversation_id": " ", "message": "Hello"},
        )[0]
        == 422
    )
    assert (
        client.request(
            "POST",
            "/chat",
            json_body={"conversation_id": "valid", "message": "  "},
        )[0]
        == 422
    )
    assert (
        client.request(
            "POST",
            "/chat",
            json_body={
                "conversation_id": "valid",
                "message": "Hello",
                "extra": "not allowed",
            },
        )[0]
        == 422
    )


def test_provider_failure_returns_safe_error_and_preserves_context(
    client, runtime_factory
) -> None:
    model = ScriptedModel([], error=ModelBehaviorError("private provider response"))
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    status_code, response = client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "failure", "message": "Hello"},
    )

    context, _ = runtime.conversations.get_or_create("failure")
    assert status_code == 502
    assert "private provider response" not in response["detail"]
    assert context.messages == ()


def test_chat_rejects_new_conversations_when_capacity_is_full(
    client, runtime_factory
) -> None:
    model = ScriptedModel([[text_output("Answer.")]])
    runtime, _ = runtime_factory(model, max_conversations=1)
    install_runtime(runtime)

    assert client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "existing", "message": "Hello"},
    )[0] == 200
    status_code, response = client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "new", "message": "Hello"},
    )

    assert status_code == 429
    assert "limit" in response["detail"]
    context, _ = runtime.conversations.get_or_create("existing")
    assert len(context.messages) == 2


def test_chat_task_tool_uses_request_database_session(
    client, db_session: Session, runtime_factory
) -> None:
    model = ScriptedModel(
        [
            [tool_call("create_task", {"title": "Session-bound task"})],
            [text_output("Task created.")],
        ]
    )
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)

    status_code, response = client.request(
        "POST",
        "/chat",
        json_body={"conversation_id": "database", "message": "Create my task"},
    )

    assert status_code == 200
    assert response["response"] == "Task created."
    assert db_session.query(Task).filter_by(title="Session-bound task").one()


def test_chat_database_dependency_failure_returns_safe_service_error(
    client, runtime_factory
) -> None:
    model = ScriptedModel([[text_output("This response must not be used.")]])
    runtime, _ = runtime_factory(model)
    install_runtime(runtime)
    previous_override = app.dependency_overrides[get_db]

    def database_failure():
        raise OperationalError("SELECT 1", {}, RuntimeError("private database detail"))

    app.dependency_overrides[get_db] = database_failure
    try:
        status_code, response = client.request(
            "POST",
            "/chat",
            json_body={"conversation_id": "db-failure", "message": "Hello"},
        )
    finally:
        app.dependency_overrides[get_db] = previous_override

    assert status_code == 503
    assert "private database detail" not in response["detail"]
    assert model.inputs == []
