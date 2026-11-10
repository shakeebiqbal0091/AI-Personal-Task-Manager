import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.database import repository
from app.database.models import TaskPriority, TaskStatus


def test_database_connection_executes_query(test_engine) -> None:
    with test_engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


def test_task_persists_between_sessions(test_engine) -> None:
    with Session(test_engine) as session:
        task = repository.create_task(
            session,
            {
                "title": "Persist this task",
                "priority": TaskPriority.MEDIUM,
                "status": TaskStatus.PENDING,
            },
        )
        task_id = task.id

    with Session(test_engine) as session:
        persisted_task = repository.get_task_by_id(session, task_id)

    assert persisted_task is not None
    assert persisted_task.title == "Persist this task"


def test_repository_rolls_back_when_commit_fails(test_engine, monkeypatch) -> None:
    with Session(test_engine) as session:
        def fail_commit() -> None:
            raise OperationalError("INSERT", {}, RuntimeError("database unavailable"))

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(OperationalError):
            repository.create_task(session, {"title": "Must roll back"})

        assert not session.in_transaction()

    with Session(test_engine) as session:
        assert repository.get_tasks(session) == []
