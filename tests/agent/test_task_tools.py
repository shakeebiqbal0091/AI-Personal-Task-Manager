import asyncio
import json
from typing import Any

import pytest
from agents import Agent, ModelSettings
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

from app.database.models import Task, TaskPriority, TaskStatus
from app.schemas.task import TaskCreate
from app.services import task_service
from app.tools.task_tools import TASK_TOOLS, TaskToolContext
from app.worker.agent import ProductivityWorker, WorkerSettings
from app.worker.context import ConversationContext


class ToolCallModel(Model):
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.inputs: list[list[TResponseInputItem]] = []
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
        items = [dict(item) for item in input] if isinstance(input, list) else []
        self.inputs.append(items)
        self.available_tools.append(tools)
        if len(self.inputs) == 1:
            output = [
                ResponseFunctionToolCall(
                    id="tool-call-1",
                    type="function_call",
                    call_id="call-1",
                    name=self.tool_name,
                    arguments=json.dumps(self.arguments),
                )
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="message-final",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[
                        ResponseOutputText(
                            type="output_text",
                            text="I have processed the task tool result.",
                            annotations=[],
                        )
                    ],
                )
            ]
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
    ) -> Any:
        if False:
            yield None


def create_worker(model: Model) -> ProductivityWorker:
    worker = ProductivityWorker(WorkerSettings(api_key="test-key"))
    worker.agent = Agent(
        name="Personal Productivity Manager",
        instructions="Use the authorized tools.",
        model=model,
        tools=list(TASK_TOOLS),
        handoffs=[],
        mcp_servers=[],
    )
    return worker


def tool_result(model: ToolCallModel) -> dict[str, Any]:
    tool_output = next(
        item
        for item in model.inputs[1]
        if item.get("type") == "function_call_output"
    )
    return json.loads(tool_output["output"])


def run_worker_tool(
    worker: ProductivityWorker,
    model: ToolCallModel,
    session: Session,
    user_message: str,
) -> str:
    return asyncio.run(
        worker.respond(user_message, ConversationContext(), session=session)
    )


def test_create_task_tool_persists_validated_service_result(db_session) -> None:
    model = ToolCallModel(
        "create_task",
        {
            "title": "  Submit project  ",
            "priority": "High",
            "category": "Work",
            "due_date": "2030-05-01T10:00:00",
            "estimated_duration": 60,
        },
    )
    worker = create_worker(model)
    try:
        response = run_worker_tool(worker, model, db_session, "Create a high priority work task.")
        result = tool_result(model)

        assert response == "I have processed the task tool result."
        assert result["success"] is True
        assert result["task"]["title"] == "Submit project"
        assert result["task"]["priority"] == "High"
        assert result["task"]["due_date"] == "2030-05-01T10:00:00"
        assert len(model.available_tools[0]) == 5
        assert db_session.query(Task).one().title == "Submit project"
    finally:
        asyncio.run(worker.close())


def test_list_task_tool_applies_existing_service_filters(db_session) -> None:
    task_service.create_task(
        db_session,
        TaskCreate(title="Work plan", priority=TaskPriority.HIGH, category="Work"),
    )
    task_service.create_task(
        db_session,
        TaskCreate(title="Personal plan", priority=TaskPriority.LOW, category="Personal"),
    )
    model = ToolCallModel(
        "list_tasks",
        {"priority": "High", "category": "Work"},
    )
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "List my high priority work tasks.")
        result = tool_result(model)

        assert result["success"] is True
        assert result["count"] == 1
        assert result["tasks"][0]["title"] == "Work plan"
    finally:
        asyncio.run(worker.close())


def test_get_task_tool_uses_exact_existing_id(db_session) -> None:
    task = task_service.create_task(db_session, TaskCreate(title="Find me"))
    model = ToolCallModel("get_task", {"task_id": task.id})
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Show me task 1.")
        result = tool_result(model)

        assert result["success"] is True
        assert result["task"]["id"] == task.id
        assert result["task"]["title"] == "Find me"
    finally:
        asyncio.run(worker.close())


def test_update_task_tool_preserves_unrequested_fields(db_session) -> None:
    task = task_service.create_task(
        db_session,
        TaskCreate(
            title="Original title",
            description="Keep this",
            priority=TaskPriority.MEDIUM,
            category="Work",
        ),
    )
    model = ToolCallModel(
        "update_task",
        {
            "task_id": task.id,
            "fields_to_update": ["title"],
            "title": "Updated title",
        },
    )
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Rename task 1 to Updated title.")
        result = tool_result(model)

        assert result["success"] is True
        assert result["task"]["title"] == "Updated title"
        assert result["task"]["description"] == "Keep this"
        assert result["task"]["category"] == "Work"
    finally:
        asyncio.run(worker.close())


def test_update_task_tool_can_clear_optional_field_explicitly(db_session) -> None:
    task = task_service.create_task(
        db_session,
        TaskCreate(title="Task with deadline", due_date="2030-05-01T10:00:00"),
    )
    model = ToolCallModel(
        "update_task",
        {
            "task_id": task.id,
            "fields_to_update": ["due_date"],
            "due_date": None,
        },
    )
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Remove the deadline from task 1.")
        result = tool_result(model)

        assert result["success"] is True
        assert result["task"]["due_date"] is None
    finally:
        asyncio.run(worker.close())


def test_complete_task_tool_uses_service_and_sets_completion_time(db_session) -> None:
    task = task_service.create_task(db_session, TaskCreate(title="Finish report"))
    model = ToolCallModel("complete_task", {"task_id": task.id})
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Mark task 1 complete.")
        result = tool_result(model)

        assert result["success"] is True
        assert result["task"]["status"] == TaskStatus.COMPLETED.value
        assert result["task"]["completed_at"] is not None
    finally:
        asyncio.run(worker.close())


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("create_task", {"title": "   "}),
        ("create_task", {"title": "Valid", "priority": "Urgent"}),
        ("get_task", {"task_id": 0}),
        (
            "update_task",
            {
                "task_id": 1,
                "fields_to_update": ["title"],
                "title": None,
            },
        ),
    ],
)
def test_invalid_tool_input_does_not_change_database(
    db_session, tool_name, arguments
) -> None:
    model = ToolCallModel(tool_name, arguments)
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Please perform this task change.")
        assert db_session.query(Task).count() == 0
    finally:
        asyncio.run(worker.close())


def test_missing_task_is_reported_as_tool_failure(db_session) -> None:
    model = ToolCallModel("get_task", {"task_id": 999})
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Retrieve task 999.")
        result = tool_result(model)

        assert result == {
            "success": False,
            "error": "No task with ID 999 was found.",
        }
    finally:
        asyncio.run(worker.close())


def test_database_failure_is_reported_without_claiming_success(
    db_session, monkeypatch
) -> None:
    def fail_create_task(*args, **kwargs):
        raise OperationalError("INSERT", {}, RuntimeError("private database detail"))

    monkeypatch.setattr(task_service, "create_task", fail_create_task)
    model = ToolCallModel("create_task", {"title": "Must not be reported as saved"})
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Create a task.")
        result = tool_result(model)

        assert result["success"] is False
        assert "database error" in result["error"]
        assert "private database detail" not in json.dumps(result)
    finally:
        asyncio.run(worker.close())


def test_task_tool_without_request_database_session_fails_safely() -> None:
    model = ToolCallModel("create_task", {"title": "No database context"})
    worker = create_worker(model)
    try:
        asyncio.run(worker.respond("Create this task.", ConversationContext()))
        result = tool_result(model)

        assert result == {
            "success": False,
            "error": "Task storage is unavailable for this request.",
        }
    finally:
        asyncio.run(worker.close())


def test_invalid_status_transition_is_reported_by_update_tool(db_session) -> None:
    task = task_service.create_task(
        db_session,
        TaskCreate(title="Completed task", status=TaskStatus.COMPLETED),
    )
    model = ToolCallModel(
        "update_task",
        {
            "task_id": task.id,
            "fields_to_update": ["status"],
            "status": "Cancelled",
        },
    )
    worker = create_worker(model)
    try:
        run_worker_tool(worker, model, db_session, "Update task status.")
        result = tool_result(model)

        assert result["success"] is False
        assert "Cannot change task status" in result["error"]
        db_session.refresh(task)
        assert task.status == TaskStatus.COMPLETED
    finally:
        asyncio.run(worker.close())


def test_deletion_is_not_registered_as_an_ai_tool() -> None:
    worker = create_worker(ToolCallModel("delete_task", {"task_id": 1}))
    try:
        assert {tool.name for tool in worker.agent.tools} == {
            "create_task",
            "list_tasks",
            "get_task",
            "update_task",
            "complete_task",
        }
    finally:
        asyncio.run(worker.close())
