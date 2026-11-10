import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from agents import RunContextWrapper, function_tool
from agents.models.interface import Tool
from pydantic import Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import Task, TaskPriority, TaskStatus
from app.schemas.task import TaskCreate, TaskFilters, TaskResponse, TaskUpdate
from app.services import task_service
from app.services.task_service import (
    InvalidStatusTransitionError,
    TaskNotFoundError,
)

logger = logging.getLogger(__name__)

MAX_SEARCH_LENGTH = 200
MAX_CATEGORY_LENGTH = 100
UPDATE_FIELDS = Literal[
    "title",
    "description",
    "priority",
    "status",
    "category",
    "due_date",
    "estimated_duration",
]


@dataclass(frozen=True)
class TaskToolContext:
    session: Session | None = None


def _failure(message: str) -> str:
    return json.dumps({"success": False, "error": message})


def _task_result(task: Task) -> str:
    return json.dumps(
        {
            "success": True,
            "task": TaskResponse.model_validate(task).model_dump(mode="json"),
        }
    )


def _validation_failure(error: ValidationError) -> str:
    errors = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in error.errors()
    ]
    return json.dumps({"success": False, "error": "Invalid task input.", "details": errors})


def _rollback_session(session: Session) -> None:
    try:
        session.rollback()
    except SQLAlchemyError as rollback_error:
        logger.error(
            "Task tool transaction rollback failed (%s)",
            type(rollback_error).__name__,
        )


@function_tool(
    name_override="create_task",
    description_override=(
        "Create a task only when the user explicitly asks to add or create one. "
        "Pass only task details provided by the user; omit an unspecified deadline. "
        "Treat the returned success field and task record as authoritative."
    ),
)
async def create_task_tool(
    context: RunContextWrapper[TaskToolContext],
    title: Annotated[str, Field(min_length=1, max_length=200)],
    description: Annotated[str | None, Field(max_length=5000)] = None,
    priority: TaskPriority = TaskPriority.MEDIUM,
    category: Annotated[str | None, Field(min_length=1, max_length=100)] = None,
    due_date: datetime | None = None,
    estimated_duration: Annotated[int | None, Field(gt=0)] = None,
) -> str:
    session = context.context.session
    if session is None:
        return _failure("Task storage is unavailable for this request.")
    try:
        task_input = TaskCreate.model_validate(
            {
                "title": title,
                "description": description,
                "priority": priority,
                "category": category,
                "due_date": due_date,
                "estimated_duration": estimated_duration,
            }
        )
        return _task_result(task_service.create_task(session, task_input))
    except ValidationError as exc:
        return _validation_failure(exc)
    except SQLAlchemyError as exc:
        _rollback_session(session)
        logger.error("Task creation failed (%s)", type(exc).__name__)
        return _failure("The task could not be saved because of a database error.")


@function_tool(
    name_override="list_tasks",
    description_override=(
        "List tasks using optional status, priority, category, text-search, or "
        "overdue filters. Do not infer filters the user did not request."
    ),
)
async def list_tasks_tool(
    context: RunContextWrapper[TaskToolContext],
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    category: Annotated[str | None, Field(max_length=MAX_CATEGORY_LENGTH)] = None,
    search: Annotated[str | None, Field(max_length=MAX_SEARCH_LENGTH)] = None,
    overdue_only: bool = False,
) -> str:
    session = context.context.session
    if session is None:
        return _failure("Task storage is unavailable for this request.")
    try:
        filters = TaskFilters.model_validate(
            {
                "status": status,
                "priority": priority,
                "category": category,
                "search": search,
                "overdue_only": overdue_only,
            }
        )
        tasks = task_service.get_tasks(session, filters)
        return json.dumps(
            {
                "success": True,
                "tasks": [
                    TaskResponse.model_validate(task).model_dump(mode="json")
                    for task in tasks
                ],
                "count": len(tasks),
            }
        )
    except ValidationError as exc:
        return _validation_failure(exc)
    except SQLAlchemyError as exc:
        _rollback_session(session)
        logger.error("Task listing failed (%s)", type(exc).__name__)
        return _failure("Tasks could not be retrieved because of a database error.")


@function_tool(
    name_override="get_task",
    description_override=(
        "Retrieve one task using its exact positive task ID supplied or identified "
        "from current task data. Never invent a task ID."
    ),
)
async def get_task_tool(
    context: RunContextWrapper[TaskToolContext],
    task_id: Annotated[int, Field(gt=0)],
) -> str:
    session = context.context.session
    if session is None:
        return _failure("Task storage is unavailable for this request.")
    try:
        return _task_result(task_service.get_task(session, task_id))
    except TaskNotFoundError:
        return _failure(f"No task with ID {task_id} was found.")
    except SQLAlchemyError as exc:
        _rollback_session(session)
        logger.error("Task retrieval failed (%s)", type(exc).__name__)
        return _failure("The task could not be retrieved because of a database error.")


@function_tool(
    name_override="update_task",
    description_override=(
        "Update only fields the user explicitly asks to change. Provide each field "
        "name in fields_to_update. A null value clears an optional field only when "
        "that field is listed. Never invent task IDs or replacement values."
    ),
)
async def update_task_tool(
    context: RunContextWrapper[TaskToolContext],
    task_id: Annotated[int, Field(gt=0)],
    fields_to_update: list[UPDATE_FIELDS],
    title: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
    description: Annotated[str | None, Field(max_length=5000)] = None,
    priority: TaskPriority | None = None,
    status: TaskStatus | None = None,
    category: Annotated[str | None, Field(min_length=1, max_length=MAX_CATEGORY_LENGTH)] = None,
    due_date: datetime | None = None,
    estimated_duration: Annotated[int | None, Field(gt=0)] = None,
) -> str:
    session = context.context.session
    if session is None:
        return _failure("Task storage is unavailable for this request.")
    if not fields_to_update:
        return _failure("Select at least one field to update.")
    try:
        requested_fields = set(fields_to_update)
        updates = TaskUpdate.model_validate(
            {
                field: value
                for field, value in {
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "status": status,
                    "category": category,
                    "due_date": due_date,
                    "estimated_duration": estimated_duration,
                }.items()
                if field in requested_fields
            }
        )
        return _task_result(task_service.update_task(session, task_id, updates))
    except ValidationError as exc:
        return _validation_failure(exc)
    except TaskNotFoundError:
        return _failure(f"No task with ID {task_id} was found.")
    except InvalidStatusTransitionError as exc:
        return _failure(str(exc))
    except SQLAlchemyError as exc:
        _rollback_session(session)
        logger.error("Task update failed (%s)", type(exc).__name__)
        return _failure("The task could not be updated because of a database error.")


@function_tool(
    name_override="complete_task",
    description_override=(
        "Mark a task completed only when the user explicitly asks to complete it. "
        "Use an exact task ID from current task data and report the returned result."
    ),
)
async def complete_task_tool(
    context: RunContextWrapper[TaskToolContext],
    task_id: Annotated[int, Field(gt=0)],
) -> str:
    session = context.context.session
    if session is None:
        return _failure("Task storage is unavailable for this request.")
    try:
        return _task_result(task_service.complete_task(session, task_id))
    except TaskNotFoundError:
        return _failure(f"No task with ID {task_id} was found.")
    except InvalidStatusTransitionError as exc:
        return _failure(str(exc))
    except SQLAlchemyError as exc:
        _rollback_session(session)
        logger.error("Task completion failed (%s)", type(exc).__name__)
        return _failure("The task could not be completed because of a database error.")


TASK_TOOLS: tuple[Tool, ...] = (
    create_task_tool,
    list_tasks_tool,
    get_task_tool,
    update_task_tool,
    complete_task_tool,
)
