import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.chat import ChatRuntime, router as chat_router
from app.api.routes import router as tasks_router
from app.database.database import Base, engine
from app.database.models import Task
from app.services.task_service import (
    InvalidStatusTransitionError,
    TaskChangedSinceConfirmationError,
    TaskNotFoundError,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine, tables=[Task.__table__])
    try:
        yield
    finally:
        runtime = getattr(application.state, "chat_runtime", None)
        if isinstance(runtime, ChatRuntime):
            await runtime.close()
            del application.state.chat_runtime


app = FastAPI(title="AI Personal Task Manager", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(chat_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(TaskNotFoundError)
async def task_not_found_handler(
    _: Request, __: TaskNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Task not found."})


@app.exception_handler(InvalidStatusTransitionError)
async def invalid_status_transition_handler(
    _: Request, exc: InvalidStatusTransitionError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(TaskChangedSinceConfirmationError)
async def task_changed_since_confirmation_handler(
    _: Request, __: TaskChangedSinceConfirmationError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "The task changed after it was presented for confirmation. "
                "Review it again before deleting."
            )
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("Database operation failed (%s)", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "A database operation failed. Please try again later."},
    )
