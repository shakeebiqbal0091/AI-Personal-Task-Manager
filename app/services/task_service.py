from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import repository
from app.database.models import Task, TaskStatus
from app.schemas.task import TaskCreate, TaskFilters, TaskUpdate

ALLOWED_STATUS_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.IN_PROGRESS,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.IN_PROGRESS: {
        TaskStatus.PENDING,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.COMPLETED: {TaskStatus.PENDING},
    TaskStatus.CANCELLED: {TaskStatus.PENDING},
}


class TaskNotFoundError(Exception):
    pass


class InvalidStatusTransitionError(Exception):
    pass


class TaskChangedSinceConfirmationError(Exception):
    pass


def create_task(session: Session, task_data: TaskCreate) -> Task:
    values = task_data.model_dump()
    if values["status"] == TaskStatus.COMPLETED:
        values["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    return repository.create_task(session, values)


def get_task(session: Session, task_id: int) -> Task:
    task = repository.get_task_by_id(session, task_id)
    if task is None:
        raise TaskNotFoundError
    return task


def get_tasks(session: Session, filters: TaskFilters) -> list[Task]:
    overdue_before = (
        datetime.now(timezone.utc).replace(tzinfo=None)
        if filters.overdue_only
        else None
    )
    return repository.get_tasks(
        session,
        status=filters.status,
        priority=filters.priority,
        category=filters.category,
        search=filters.search,
        overdue_before=overdue_before,
    )


def update_task(session: Session, task_id: int, updates: TaskUpdate) -> Task:
    task = get_task(session, task_id)
    values = updates.model_dump(exclude_unset=True)
    new_status = values.get("status")
    if new_status is not None and new_status != task.status:
        if new_status not in ALLOWED_STATUS_TRANSITIONS[task.status]:
            raise InvalidStatusTransitionError(
                f"Cannot change task status from {task.status.value} "
                f"to {new_status.value}."
            )
        values["completed_at"] = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            if new_status == TaskStatus.COMPLETED
            else None
        )
    return repository.update_task(session, task, values)


def complete_task(session: Session, task_id: int) -> Task:
    return update_task(
        session,
        task_id,
        TaskUpdate(status=TaskStatus.COMPLETED),
    )


def delete_task(
    session: Session, task_id: int, expected_updated_at: datetime
) -> None:
    task = get_task(session, task_id)
    if task.updated_at != expected_updated_at:
        raise TaskChangedSinceConfirmationError
    if not repository.delete_task(session, task_id, expected_updated_at):
        raise TaskChangedSinceConfirmationError
