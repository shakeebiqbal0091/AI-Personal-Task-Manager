from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.worker.context import MAX_MESSAGE_LENGTH


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("conversation_id", "message", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ChatResponse(BaseModel):
    conversation_id: str
    response: str
