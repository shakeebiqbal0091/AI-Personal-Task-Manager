from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.models import TaskPriority, TaskStatus


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    category: str | None = Field(default=None, min_length=1, max_length=100)
    due_date: datetime | None = None
    estimated_duration: int | None = Field(default=None, gt=0)

    @field_validator("title", "category", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_date")
    @classmethod
    def normalize_due_date(cls, value: datetime | None) -> datetime | None:
        return _normalize_datetime(value)


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)
    due_date: datetime | None = None
    estimated_duration: int | None = Field(default=None, gt=0)

    @field_validator("title", "priority", "status", mode="before")
    @classmethod
    def reject_null_required_updates(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be null.")
        return value

    @field_validator("title", "category", mode="before")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

    @field_validator("due_date")
    @classmethod
    def normalize_due_date(cls, value: datetime | None) -> datetime | None:
        return _normalize_datetime(value)

    @model_validator(mode="after")
    def require_update_field(self) -> "TaskUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    priority: TaskPriority
    status: TaskStatus
    category: str | None
    due_date: datetime | None
    estimated_duration: int | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)
    search: str | None = Field(default=None, min_length=1, max_length=200)
    overdue_only: bool = False

    @field_validator("category", "search", mode="before")
    @classmethod
    def strip_filter_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value
