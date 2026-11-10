from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import Task, TaskPriority, TaskStatus


def _commit(session: Session) -> None:
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise


def create_task(session: Session, task_data: dict[str, object]) -> Task:
    task = Task(**task_data)
    session.add(task)
    _commit(session)
    session.refresh(task)
    return task


def get_task_by_id(session: Session, task_id: int) -> Task | None:
    return session.get(Task, task_id)


def get_tasks(
    session: Session,
    *,
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    category: str | None = None,
    search: str | None = None,
    overdue_before: datetime | None = None,
) -> list[Task]:
    statement = select(Task)
    if status is not None:
        statement = statement.where(Task.status == status)
    if priority is not None:
        statement = statement.where(Task.priority == priority)
    if category is not None:
        statement = statement.where(Task.category == category)
    if search is not None:
        pattern = f"%{search}%"
        statement = statement.where(
            or_(Task.title.ilike(pattern), Task.description.ilike(pattern))
        )
    if overdue_before is not None:
        statement = statement.where(
            Task.due_date < overdue_before,
            Task.status.in_((TaskStatus.PENDING, TaskStatus.IN_PROGRESS)),
        )
    statement = statement.order_by(Task.id)
    return list(session.scalars(statement))


def update_task(
    session: Session, task: Task, task_data: dict[str, object]
) -> Task:
    for field, value in task_data.items():
        setattr(task, field, value)
    _commit(session)
    session.refresh(task)
    return task


def delete_task(
    session: Session, task_id: int, expected_updated_at: datetime
) -> bool:
    result = session.execute(
        delete(Task).where(
            Task.id == task_id,
            Task.updated_at == expected_updated_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    _commit(session)
    return True
