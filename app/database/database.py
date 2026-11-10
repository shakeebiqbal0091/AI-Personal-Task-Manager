import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./task_manager.db")

engine: Engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
Base = declarative_base()


def get_db() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
