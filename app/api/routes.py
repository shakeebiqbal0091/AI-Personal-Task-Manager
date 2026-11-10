from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import Task
from app.schemas.task import TaskCreate, TaskFilters, TaskResponse, TaskUpdate
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    task_data: TaskCreate,
    session: Annotated[Session, Depends(get_db)],
) -> Task:
    return task_service.create_task(session, task_data)


@router.get("", response_model=list[TaskResponse])
def get_tasks(
    filters: Annotated[TaskFilters, Query()],
    session: Annotated[Session, Depends(get_db)],
) -> list[Task]:
    return task_service.get_tasks(session, filters)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: Annotated[int, Path(gt=0)],
    session: Annotated[Session, Depends(get_db)],
) -> Task:
    return task_service.get_task(session, task_id)


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: Annotated[int, Path(gt=0)],
    updates: TaskUpdate,
    session: Annotated[Session, Depends(get_db)],
) -> Task:
    return task_service.update_task(session, task_id, updates)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: Annotated[int, Path(gt=0)],
    confirm: Annotated[bool, Query()] = False,
    expected_updated_at: Annotated[datetime | None, Query()] = None,
    session: Session = Depends(get_db),
) -> Response:
    if not confirm or expected_updated_at is None:
        task = task_service.get_task(session, task_id)
        task_data = TaskResponse.model_validate(task).model_dump(mode="json")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "Review this task, then retry with confirm=true and "
                    "the returned updated_at value."
                ),
                "task": task_data,
            },
        )
    task_service.delete_task(session, task_id, expected_updated_at)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
