from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum as SqlEnum,
    Integer,
    String,
    Text,
    func,
)

from app.database.database import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TaskPriority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TaskStatus(str, Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_tasks_title_not_blank"),
        CheckConstraint("length(title) <= 200", name="ck_tasks_title_max_length"),
        CheckConstraint(
            "category IS NULL OR length(category) <= 100",
            name="ck_tasks_category_max_length",
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 5000",
            name="ck_tasks_description_max_length",
        ),
        CheckConstraint(
            "estimated_duration IS NULL OR estimated_duration > 0",
            name="ck_tasks_estimated_duration_positive",
        ),
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(
        SqlEnum(
            TaskPriority,
            name="task_priority",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=TaskPriority.MEDIUM,
        server_default=TaskPriority.MEDIUM.value,
    )
    status = Column(
        SqlEnum(
            TaskStatus,
            name="task_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=TaskStatus.PENDING,
        server_default=TaskStatus.PENDING.value,
    )
    category = Column(String(100), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    estimated_duration = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.current_timestamp(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.current_timestamp(),
        onupdate=_utc_now,
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
