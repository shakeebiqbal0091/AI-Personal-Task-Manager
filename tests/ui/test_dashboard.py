from unittest.mock import MagicMock, Mock

from app.schemas.chat import ChatResponse
from app.schemas.task import TaskResponse
from app.ui import dashboard
from app.ui.api_client import APIError


def make_task() -> TaskResponse:
    return TaskResponse.model_validate(
        {
            "id": 1,
            "title": "Prepare report",
            "description": "Draft the weekly summary",
            "priority": "High",
            "status": "Pending",
            "category": "Work",
            "due_date": "2026-12-01T10:00:00",
            "estimated_duration": 30,
            "created_at": "2026-11-01T09:00:00",
            "updated_at": "2026-11-01T09:00:00",
            "completed_at": None,
        }
    )


class StubAPIClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.filters: list[dict[str, str | bool]] = []
        self.tasks = [make_task()]
        self.chat_error: APIError | None = None

    def send_message(self, conversation_id: str, message: str) -> ChatResponse:
        self.messages.append((conversation_id, message))
        if self.chat_error:
            raise self.chat_error
        return ChatResponse(
            conversation_id=conversation_id,
            response="I added the task.",
        )

    def list_tasks(
        self,
        filters: dict[str, str | bool] | None = None,
    ) -> list[TaskResponse]:
        self.filters.append(filters or {})
        return self.tasks


def test_message_submission_displays_user_and_assistant_messages() -> None:
    client = StubAPIClient()
    state: dict[str, object] = {}
    dashboard.initialize_session_state(state)

    submitted = dashboard.submit_message(client, state, "  Add a task  ")

    assert submitted is True
    assert client.messages == [(state["conversation_id"], "Add a task")]
    assert state["messages"] == [
        {"role": "user", "content": "Add a task"},
        {"role": "assistant", "content": "I added the task."},
    ]
    assert state["refresh_tasks"] is True


def test_existing_conversation_id_is_preserved_in_session_state() -> None:
    state: dict[str, object] = {
        "conversation_id": "keep-this-id",
        "messages": [{"role": "user", "content": "Earlier"}],
    }

    dashboard.initialize_session_state(state)
    client = StubAPIClient()
    dashboard.submit_message(client, state, "Continue")

    assert client.messages == [("keep-this-id", "Continue")]


def test_new_conversation_generates_id_and_clears_local_messages() -> None:
    state: dict[str, object] = {
        "conversation_id": "old-conversation",
        "messages": [{"role": "user", "content": "Earlier"}],
    }

    new_id = dashboard.start_new_conversation(state)

    assert new_id != "old-conversation"
    assert state["conversation_id"] == new_id
    assert state["messages"] == []


def test_render_chat_history_uses_user_and_assistant_roles(
    monkeypatch,
) -> None:
    chat_message = Mock(return_value=MagicMock())
    markdown = Mock()
    monkeypatch.setattr(dashboard.st, "chat_message", chat_message)
    monkeypatch.setattr(dashboard.st, "markdown", markdown)

    dashboard.render_chat_messages(
        [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there."},
        ]
    )

    assert [call.args[0] for call in chat_message.call_args_list] == [
        "user",
        "assistant",
    ]
    assert [call.args[0] for call in markdown.call_args_list] == [
        "Hello",
        "Hi there.",
    ]


def test_chat_history_displays_api_errors(monkeypatch) -> None:
    error = Mock()
    monkeypatch.setattr(dashboard.st, "chat_message", Mock(return_value=MagicMock()))
    monkeypatch.setattr(dashboard.st, "markdown", Mock())
    monkeypatch.setattr(dashboard.st, "error", error)

    dashboard.render_chat_messages(
        [
            {
                "role": "user",
                "content": "Create a task",
                "error": "The backend is unavailable.",
            }
        ]
    )

    error.assert_called_once_with("The backend is unavailable.")


def test_api_failure_is_visible_without_claiming_success() -> None:
    client = StubAPIClient()
    client.chat_error = APIError("The AI provider could not complete the request.")
    state: dict[str, object] = {}
    dashboard.initialize_session_state(state)

    submitted = dashboard.submit_message(client, state, "Create a task")

    assert submitted is False
    assert state["messages"] == [
        {
            "role": "user",
            "content": "Create a task",
            "error": "The AI provider could not complete the request.",
        }
    ]
    assert state["refresh_tasks"] is True


def test_duplicate_submission_is_ignored_while_a_request_is_in_flight() -> None:
    client = StubAPIClient()
    state: dict[str, object] = {
        "conversation_id": "conversation",
        "messages": [],
        "is_sending": True,
    }

    assert dashboard.submit_message(client, state, "Create a task") is False
    assert client.messages == []
    assert state["messages"] == []


def test_task_filters_are_retrieved_and_successful_chat_marks_them_stale() -> None:
    client = StubAPIClient()
    state: dict[str, object] = {}
    dashboard.initialize_session_state(state)
    filters = {"status": "Pending", "priority": "High", "overdue_only": True}

    tasks = dashboard.refresh_task_data(client, state, filters)
    assert tasks == client.tasks
    assert client.filters == [filters]

    dashboard.refresh_task_data(client, state, filters)
    assert client.filters == [filters]

    assert dashboard.submit_message(client, state, "Add a task") is True
    tasks = dashboard.refresh_task_data(client, state, filters)

    assert tasks == client.tasks
    assert client.filters == [filters, filters]


def test_task_api_error_is_returned_without_showing_stale_task_data() -> None:
    client = StubAPIClient()
    state: dict[str, object] = {"tasks": [make_task()], "refresh_tasks": True}
    client.list_tasks = Mock(side_effect=APIError("The backend is unavailable."))

    tasks = dashboard.refresh_task_data(client, state, {"status": "Pending"})

    assert tasks is None
    assert state["tasks"] is None
    assert state["task_error"] == "The backend is unavailable."


def test_invalid_backend_configuration_is_shown_without_traceback(
    monkeypatch,
) -> None:
    error = APIError("The backend URL is invalid.")
    monkeypatch.setattr(dashboard.st, "set_page_config", Mock())
    monkeypatch.setattr(dashboard, "APIClient", Mock(side_effect=error))
    display_error = Mock()
    monkeypatch.setattr(dashboard.st, "error", display_error)

    dashboard.main()

    display_error.assert_called_once_with(error.message)
